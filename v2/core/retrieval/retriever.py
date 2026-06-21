"""V2 hybrid retriever (Step 4).

Combines two search legs over ``knowledge_items`` and fuses them with Reciprocal
Rank Fusion (RRF, k=60):

  1. Semantic — sqlite-vec KNN over ``knowledge_vectors`` (L2-normalized vectors,
     so distance ranks like cosine).
  2. Keyword — FTS5 ``bm25()`` over the generated ``search_text`` column.

RRF makes the two legs complementary: short, structured records (e.g. a contact
card) that vector search alone ranks poorly get surfaced by the keyword leg, and
vice-versa. Optional org-subtree and item-type filters scope retrieval to any
node of the organization tree.

Generation (Ollama llama3.1) is unchanged from v1 and lives elsewhere; this
module only does retrieval.

Note: the public API is synchronous. Bot integration will wrap ``retrieve`` in a
thread executor so the Ollama embed call never blocks the discord event loop.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

logger = logging.getLogger(__name__)

# Optional per-query retrieval trace (set RETRIEVAL_DEBUG_LOG=true): what was
# fetched, the fused top-N, and their scores/legs/ids — to debug LLM answers.
RETRIEVAL_DEBUG_FILE = Path(__file__).resolve().parents[3] / "logs" / "retrieval_debug.log"

RRF_K = 60
# Smaller K for the cross-encoder leg in rerank fusion → CE differences matter more, so it
# dominates ordering while the fused leg (K=60) keeps an exact-match floor.
RERANK_CE_K = 10
# NOTE: the old `contact` type boost was removed (2026-06-15). It was a band-aid to lift
# officer contact cards; now officers live in the KG (answered by the structured router)
# and the remaining campus-office contacts rank fine on their own — the boost only caused
# over-ranking (e.g. "NJIT Library" winning "robotics at NJIT", campus "Office of…" cards
# burying the GSA office doc). event_info keeps a small boost (short records, unflagged).
DEFAULT_EVENT_BOOST = 1.2
# Types kept OUT of the default answer corpus. Publications are ~78% of the corpus
# and pure noise for almost every student-facing question (they bury the bios that
# actually answer "are there robotic labs"); raw personal-website dumps are long and
# low-signal. They stay embedded and are still reachable via an explicit item_types
# whitelist or a publications-intent route — just not in general answers. Admin-tunable
# via the `retriever.exclude_types` setting (comma-separated; empty string = exclude none).
DEFAULT_EXCLUDE_TYPES = frozenset({"publication", "webpage"})
# Candidate pool per leg for fusion — deliberately decoupled from `limit`.
# "pool" = how wide we search; "limit" = how many we return. A boosted item that
# is strong in only one leg must still enter the pool to be liftable. Never drops
# below MIN_POOL_SIZE regardless of the settings value or limit.
MIN_POOL_SIZE = 40
DEFAULT_POOL_SIZE = 40
# sqlite-vec vec0 hard cap on KNN `k` (LIMIT) per query. Fetching more raises
# "k value in knn query too large". We cap the semantic leg's fetch at this.
_VEC_KNN_MAX = 4096
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Pure function words dropped from the FTS keyword leg so BM25 ranks on content
# words. Deliberately conservative — keeps borderline-content words like "get",
# "money", "fund". The semantic leg still sees the full query.
_STOPWORDS = frozenset(
    "a an and or of to for in on at is are am was were be been do does did "
    "i me my you your he she it its we they them this that these those "
    "how what who whom where when which why with as by from "
    "there here have has had been being".split()
)


@dataclass
class RetrievedChunk:
    item_id: int
    title: str | None
    type: str
    content: str
    org_path: str          # e.g. "New Jersey Institute of Technology > GSA"
    similarity: float | None  # cosine-equiv (0..1); None if keyword-only hit
    source: str            # 'semantic' | 'keyword' | 'hybrid' | 'expanded'
    rrf_score: float
    source_url: str | None = None  # provenance carried to the prompt (R4)
    verified: bool = True          # False = first-layer LLM draft, not authoritative


def _meta(metadata) -> dict:
    if not metadata:
        return {}
    try:
        return json.loads(metadata)
    except (TypeError, ValueError):
        return {}


def _meta_entity_id(metadata) -> str | None:
    return _meta(metadata).get("entity_id")


def _meta_verified(metadata) -> bool:
    return bool(_meta(metadata).get("verified", True))


def _fts_match_expr(query: str) -> str | None:
    """Turn a free-text query into a safe FTS5 OR-of-terms match expression.

    Drops function words so BM25 ranks on content terms. Falls back to the full
    token set if a query is *all* stopwords (so we never produce an empty match).
    """
    tokens = [t.lower() for t in _TOKEN.findall(query)]
    if not tokens:
        return None
    content = [t for t in tokens if t not in _STOPWORDS]
    terms = content or tokens
    return " OR ".join(f'"{t}"' for t in terms)


class V2Retriever:
    def __init__(self, conn, embedder, reranker=None):
        self.conn = conn
        self.embedder = embedder
        self.reranker = reranker
        self._org_path_cache: dict[int, str] = {}
        self.debug_log = os.getenv("RETRIEVAL_DEBUG_LOG", "false").lower() == "true"
        # event_info gets a small boost (short records lose to long FAQ/policy text on
        # semantic distance alone). The old contact boost was removed — see note at top.
        # Admin-tunable via the settings table.
        self.event_boost = self._load_boost("retriever.event_boost", DEFAULT_EVENT_BOOST)
        # Admin-tunable pool size, but never below MIN_POOL_SIZE.
        self.pool_size = max(MIN_POOL_SIZE,
                             int(self._load_boost("retriever.pool_size", DEFAULT_POOL_SIZE)))
        # Types excluded from the default answer corpus (see DEFAULT_EXCLUDE_TYPES).
        self.exclude_types = self._load_exclude("retriever.exclude_types", DEFAULT_EXCLUDE_TYPES)
        # Cross-encoder rerank of the fused pool (admin-tunable; instant kill-switch).
        self.rerank_enabled = self._load_bool("retriever.rerank_enabled", True)
        # Rerank the FULL fused pool by default (senior review S2), never below pool_size.
        self.rerank_pool = max(self.pool_size,
                               int(self._load_boost("retriever.rerank_pool", self.pool_size)))

    def _load_bool(self, key: str, default: bool) -> bool:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1", (key,)
        ).fetchone()
        if not row or row["value"] is None:
            return default
        return str(row["value"]).strip().lower() in ("1", "true", "yes", "on")

    def _load_exclude(self, key: str, default: frozenset) -> frozenset:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1", (key,)
        ).fetchone()
        if not row or row["value"] is None:
            return default
        # explicit setting wins, including an empty string meaning "exclude nothing"
        return frozenset(t.strip().lower() for t in str(row["value"]).split(",") if t.strip())

    def _load_boost(self, key: str, default: float) -> float:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1", (key,)
        ).fetchone()
        try:
            return float(row["value"]) if row and row["value"] is not None else default
        except (TypeError, ValueError):
            return default

    def _boost_for(self, item_type: str) -> float:
        if item_type == "event_info":
            return self.event_boost
        return 1.0

    def _rerank(self, query, ranked, rows):
        """Re-fuse the fused pool with the cross-encoder. We RRF-fuse the CE ranking with
        the existing fused ranking (same RRF the retriever already uses) rather than letting
        CE override — pure CE reorder *demotes* exact-keyword facts that bm25 nailed
        (regressions), while RRF-fusion keeps those wins AND lifts the semantically-correct
        chunk. type_boost stays a multiplicative prior (senior review C2). Returns `ranked`
        unchanged on any miss — reranking is strictly additive."""
        if not self.rerank_enabled or self.reranker is None or len(ranked) < 2:
            return ranked
        window = ranked[: self.rerank_pool]
        passages = [rows[iid]["content"] or "" for iid, _ in window]
        ce = self.reranker.score(query, passages)
        if ce is None:
            return ranked
        fused_rank = {iid: r for r, (iid, _) in enumerate(window, start=1)}
        ce_order = sorted(range(len(window)), key=lambda i: -ce[i])
        ce_rank = {window[i][0]: r for r, i in enumerate(ce_order, start=1)}

        # Asymmetric RRF: the CE leg gets a smaller K so it dominates ordering (fixes the
        # semantic "wrong chunk" misses), while the existing fused leg stays a floor so
        # exact-keyword facts bm25 nailed aren't demoted (avoids regressions).
        def _score(iid):
            rrf = 1.0 / (RRF_K + fused_rank[iid]) + 1.0 / (RERANK_CE_K + ce_rank[iid])
            return rrf * self._boost_for(rows[iid]["type"])

        rescored = sorted(((iid, _score(iid)) for iid, _ in window), key=lambda kv: -kv[1])
        return rescored + ranked[self.rerank_pool:]

    # ── organization tree helpers ───────────────────────────────────────────
    def _subtree_ids(self, org_id: int) -> list[int]:
        rows = self.conn.execute(
            "WITH RECURSIVE subtree(id) AS ("
            "  SELECT id FROM organizations WHERE id=? "
            "  UNION ALL "
            "  SELECT o.id FROM organizations o JOIN subtree s ON o.parent_id=s.id"
            ") SELECT id FROM subtree",
            (org_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def org_path(self, org_id: int) -> str:
        if org_id in self._org_path_cache:
            return self._org_path_cache[org_id]
        rows = self.conn.execute(
            "WITH RECURSIVE up(id,name,parent_id) AS ("
            "  SELECT id,name,parent_id FROM organizations WHERE id=? "
            "  UNION ALL "
            "  SELECT o.id,o.name,o.parent_id FROM organizations o JOIN up ON o.id=up.parent_id"
            ") SELECT name FROM up",
            (org_id,),
        ).fetchall()
        path = " > ".join(r["name"] for r in reversed(rows))
        self._org_path_cache[org_id] = path
        return path

    # ── filtering ───────────────────────────────────────────────────────────
    def _allowed_ids(self, org_id, org_subtree, item_types, exclude_types=None) -> set[int] | None:
        """Return the set of item ids permitted by the filters, or None (no filter)."""
        if org_id is None and not item_types and not exclude_types:
            return None
        clauses, params = ["is_active=1"], []
        if org_id is not None:
            ids = self._subtree_ids(org_id) if org_subtree else [org_id]
            clauses.append(f"org_id IN ({','.join('?' * len(ids))})")
            params += ids
        if item_types:
            clauses.append(f"type IN ({','.join('?' * len(item_types))})")
            params += list(item_types)
        if exclude_types:
            clauses.append(f"type NOT IN ({','.join('?' * len(exclude_types))})")
            params += list(exclude_types)
        rows = self.conn.execute(
            f"SELECT id FROM knowledge_items WHERE {' AND '.join(clauses)}", params
        ).fetchall()
        return {r["id"] for r in rows}

    # ── search legs ─────────────────────────────────────────────────────────
    def _semantic(self, qvec, fetch: int, allowed: set[int] | None):
        rows = self.conn.execute(
            "SELECT item_id, distance FROM knowledge_vectors "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(qvec), fetch),
        ).fetchall()
        return [
            (r["item_id"], r["distance"])
            for r in rows
            if allowed is None or r["item_id"] in allowed
        ]

    def _keyword(self, query: str, fetch: int, allowed: set[int] | None):
        expr = _fts_match_expr(query)
        if not expr:
            return []
        clauses, params = ["knowledge_fts MATCH ?", "ki.is_active=1"], [expr]
        if allowed is not None:
            clauses.append(f"ki.id IN ({','.join('?' * len(allowed))})")
            params += list(allowed)
        rows = self.conn.execute(
            "SELECT ki.id, bm25(knowledge_fts) AS score FROM knowledge_fts "
            "JOIN knowledge_items ki ON ki.id=knowledge_fts.rowid "
            f"WHERE {' AND '.join(clauses)} ORDER BY score LIMIT ?",
            params + [fetch],
        ).fetchall()
        return [(r["id"], r["score"]) for r in rows]

    # ── public API ──────────────────────────────────────────────────────────
    def retrieve(
        self,
        query: str,
        org_id: int | None = None,
        org_subtree: bool = True,
        item_types: list[str] | None = None,
        limit: int = 5,
        group_by_entity: bool = True,
        exclude_types: list[str] | None = None,
        query_vec: list[float] | None = None,
    ) -> list[RetrievedChunk]:
        # Default answer corpus drops noise-heavy types (publications, raw webpages).
        # An explicit item_types whitelist already constrains, so exclusion is skipped
        # then; a caller can override with exclude_types (e.g. [] to search everything).
        eff_exclude = None if item_types else (
            exclude_types if exclude_types is not None else self.exclude_types)
        allowed = self._allowed_ids(org_id, org_subtree, item_types, eff_exclude)
        total_active = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1"
        ).fetchone()[0]
        # Fusion pool is a FIXED width (not tied to `limit`). When filtering, fetch
        # the whole corpus from each leg so filtering is exact — BUT sqlite-vec's vec0
        # KNN caps k at 4096 (_VEC_KNN_MAX). Past that we fetch the 4096 nearest and
        # filter; that's the top ~83% of the corpus by distance, far more than enough to
        # contain the handful of allowed hits the fusion needs. (FTS bm25 has no such cap.)
        pool = self.pool_size
        sem_fetch = min(total_active, _VEC_KNN_MAX) if allowed is not None else min(pool, _VEC_KNN_MAX)

        qvec = query_vec if query_vec is not None else self.embedder.embed_query(query)
        sem = self._semantic(qvec, sem_fetch, allowed) if qvec else []
        kw = self._keyword(query, (total_active if allowed is not None else pool), allowed)

        scores: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        sim: dict[int, float] = {}
        for rank, (iid, dist) in enumerate(sem, start=1):
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (RRF_K + rank)
            sources.setdefault(iid, set()).add("semantic")
            sim[iid] = max(0.0, 1.0 - (dist * dist) / 2.0)  # normalized-L2 -> cosine
        for rank, (iid, _score) in enumerate(kw, start=1):
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (RRF_K + rank)
            sources.setdefault(iid, set()).add("keyword")

        if not scores:
            logger.debug(
                "retrieve(%r) → 0 results | org_scope: %s | vec: %d | bm25: %d | rrf+boost: 0",
                query[:50], org_id or "all", len(sem), len(kw),
            )
            return []

        # Hydrate every candidate (small set) so we can apply the type boost
        # using each item's type, then re-sort by the boosted score.
        cand_ids = list(scores.keys())
        rows = {
            r["id"]: r
            for r in self.conn.execute(
                f"SELECT id,title,type,content,org_id,metadata,source_url "
                f"FROM knowledge_items WHERE id IN ({','.join('?' * len(cand_ids))})",
                cand_ids,
            )
        }
        for iid in cand_ids:
            scores[iid] *= self._boost_for(rows[iid]["type"])

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        ranked = self._rerank(query, ranked, rows)
        if group_by_entity:
            final = self._diversify_and_expand(ranked, rows, limit, item_types)
        else:
            final = [(iid, s, False) for iid, s in ranked[:limit]]

        chunks = []
        for iid, boosted, expanded in final:
            r = rows[iid]
            if expanded:
                source = "expanded"
            else:
                src = sources[iid]
                source = "hybrid" if len(src) == 2 else next(iter(src))
            chunks.append(RetrievedChunk(
                item_id=iid, title=r["title"], type=r["type"], content=r["content"],
                org_path=self.org_path(r["org_id"]),
                similarity=None if expanded else sim.get(iid),
                source=source, rrf_score=boosted,
                source_url=r["source_url"], verified=_meta_verified(r["metadata"]),
            ))

        logger.debug(
            "retrieve(%r) → %d results | org_scope: %s | vec: %d | bm25: %d | rrf+boost: %d",
            query[:50], len(chunks), org_id or "all", len(sem), len(kw), len(chunks),
        )
        if self.debug_log:
            self._write_trace(query, org_id, len(sem), len(kw), chunks)
        return chunks

    # ── entity grouping + parent expansion (R3) ───────────────────────────────
    @staticmethod
    def _entity_key(row) -> str | None:
        return _meta_entity_id(row["metadata"])

    def _diversify_and_expand(self, ranked, rows, limit, item_types=None):
        """Decomposition makes one entity many items, so a naive top-`limit` can be
        five publications by one professor. Diversify by entity (round-robin across
        entities, best item first), then expand each chosen entity with its profile
        item so the LLM always has the person's name/title context (small-to-big).

        Returns ``[(item_id, score, is_expanded)]`` in reading order. With no
        ``entity_id`` metadata every item is its own bucket, so this is a no-op
        slice to `limit`. The result can exceed `limit` by up to the number of
        distinct entities (each may pull in one extra profile item).

        Parent expansion is scoped to the entity's own ``org_id`` (never crosses
        tenants on a shared slug) and is skipped entirely when the caller restricted
        ``item_types`` — injecting a ``profile`` would violate that filter.
        """
        expand = not item_types  # an explicit type filter forbids adding profiles
        buckets: dict[str, list[tuple[int, float]]] = {}
        order: list[str] = []  # bucket keys in best-first order
        for iid, score in ranked:
            ekey = self._entity_key(rows[iid]) or f"__item_{iid}"
            if ekey not in buckets:
                buckets[ekey] = []
                order.append(ekey)
            buckets[ekey].append((iid, score))

        # round-robin: best of each entity, then second-best, … up to `limit`
        primaries: list[tuple[int, float]] = []
        idx = {k: 0 for k in order}
        while len(primaries) < limit:
            progressed = False
            for k in order:
                if idx[k] < len(buckets[k]):
                    primaries.append(buckets[k][idx[k]])
                    idx[k] += 1
                    progressed = True
                    if len(primaries) >= limit:
                        break
            if not progressed:
                break

        selected = {iid for iid, _ in primaries}
        # which real entities are in the result, their org, and do they already
        # have a profile among the primaries?
        entity_order: list[str] = []
        entity_org: dict[str, int] = {}
        has_profile: set[str] = set()
        for iid, _ in primaries:
            ek = self._entity_key(rows[iid])
            if not ek:
                continue
            if ek not in entity_order:
                entity_order.append(ek)
                entity_org[ek] = rows[iid]["org_id"]
            if rows[iid]["type"] == "profile":
                has_profile.add(ek)

        expansion: dict[str, int] = {}
        if expand:
            for ek in entity_order:
                if ek in has_profile:
                    continue
                prow = self.conn.execute(
                    "SELECT id,title,type,content,org_id,metadata,source_url "
                    "FROM knowledge_items WHERE is_active=1 AND type='profile' "
                    "AND org_id=? AND json_extract(metadata,'$.entity_id')=? LIMIT 1",
                    (entity_org[ek], ek)).fetchone()
                if prow and prow["id"] not in selected:
                    rows[prow["id"]] = prow
                    expansion[ek] = prow["id"]

        # assemble: each entity's profile sits just before that entity's first item
        final: list[tuple[int, float, bool]] = []
        emitted: set[str] = set()
        for iid, score in primaries:
            ek = self._entity_key(rows[iid])
            if ek and ek in expansion and ek not in emitted:
                final.append((expansion[ek], 0.0, True))
                emitted.add(ek)
            final.append((iid, score, False))
        return final

    def _write_trace(self, query, org_id, n_sem, n_kw, chunks) -> None:
        """Append a per-query retrieval trace to logs/retrieval_debug.log so an LLM
        answer can be debugged: what was fetched and which items won (with ids,
        legs, scores). Never raises."""
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        try:
            RETRIEVAL_DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(RETRIEVAL_DEBUG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[{ts}] QUERY: {query!r}  org_scope={org_id or 'all'}  "
                        f"pool: vec={n_sem} bm25={n_kw} -> {len(chunks)} fused\n")
                for i, c in enumerate(chunks, 1):
                    sim = f"{c.similarity:.0%}" if c.similarity is not None else "—"
                    src = (c.org_path or "").split(" > ")[-1] or "—"
                    f.write(f"  {i}. doc_id={c.item_id} [{c.type}] {c.title!r}  "
                            f"src={src}  leg={c.source}  rrf+boost={c.rrf_score:.4f}  sim={sim}\n")
        except Exception as exc:  # tracing must never break retrieval — but be visible
            logger.warning("retrieval trace write failed: %s", exc)

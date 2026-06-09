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

import logging
import re
from dataclasses import dataclass

import sqlite_vec

logger = logging.getLogger(__name__)

RRF_K = 60
DEFAULT_CONTACT_BOOST = 1.5
DEFAULT_EVENT_BOOST = 1.2
# Candidate pool per leg for fusion — deliberately decoupled from `limit`.
# "pool" = how wide we search; "limit" = how many we return. A boosted item that
# is strong in only one leg must still enter the pool to be liftable. Never drops
# below MIN_POOL_SIZE regardless of the settings value or limit.
MIN_POOL_SIZE = 40
DEFAULT_POOL_SIZE = 40
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Pure function words dropped from the FTS keyword leg so BM25 ranks on content
# words. Deliberately conservative — keeps borderline-content words like "get",
# "money", "fund". The semantic leg still sees the full query.
_STOPWORDS = frozenset(
    "a an and or of to for in on at is are am was were be been do does did "
    "i me my you your he she it its we they them this that these those "
    "how what who whom where when which why with as by from".split()
)


@dataclass
class RetrievedChunk:
    item_id: int
    title: str | None
    type: str
    content: str
    org_path: str          # e.g. "New Jersey Institute of Technology > GSA"
    similarity: float | None  # cosine-equiv (0..1); None if keyword-only hit
    source: str            # 'semantic' | 'keyword' | 'hybrid'
    rrf_score: float


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
    def __init__(self, conn, embedder):
        self.conn = conn
        self.embedder = embedder
        self._org_path_cache: dict[int, str] = {}
        # Type boosts correct for content-length asymmetry: short structured
        # records (contacts, events) lose to long FAQ/policy text on semantic
        # distance alone. Admin-tunable via the settings table.
        self.contact_boost = self._load_boost("retriever.contact_boost", DEFAULT_CONTACT_BOOST)
        self.event_boost = self._load_boost("retriever.event_boost", DEFAULT_EVENT_BOOST)
        # Admin-tunable pool size, but never below MIN_POOL_SIZE.
        self.pool_size = max(MIN_POOL_SIZE,
                             int(self._load_boost("retriever.pool_size", DEFAULT_POOL_SIZE)))

    def _load_boost(self, key: str, default: float) -> float:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1", (key,)
        ).fetchone()
        try:
            return float(row["value"]) if row and row["value"] is not None else default
        except (TypeError, ValueError):
            return default

    def _boost_for(self, item_type: str) -> float:
        if item_type == "contact":
            return self.contact_boost
        if item_type == "event_info":
            return self.event_boost
        return 1.0

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
    def _allowed_ids(self, org_id, org_subtree, item_types) -> set[int] | None:
        """Return the set of item ids permitted by the filters, or None (no filter)."""
        if org_id is None and not item_types:
            return None
        clauses, params = ["is_active=1"], []
        if org_id is not None:
            ids = self._subtree_ids(org_id) if org_subtree else [org_id]
            clauses.append(f"org_id IN ({','.join('?' * len(ids))})")
            params += ids
        if item_types:
            clauses.append(f"type IN ({','.join('?' * len(item_types))})")
            params += list(item_types)
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
    ) -> list[RetrievedChunk]:
        allowed = self._allowed_ids(org_id, org_subtree, item_types)
        total_active = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1"
        ).fetchone()[0]
        # Fusion pool is a FIXED width (not tied to `limit`). When filtering, fetch
        # the whole corpus from the KNN leg so filtering is exact. Trivial at this
        # scale; for a large corpus use a vec0 partition.
        pool = self.pool_size
        sem_fetch = total_active if allowed is not None else pool

        qvec = self.embedder.embed_query(query)
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
                f"SELECT id,title,type,content,org_id FROM knowledge_items "
                f"WHERE id IN ({','.join('?' * len(cand_ids))})",
                cand_ids,
            )
        }
        for iid in cand_ids:
            scores[iid] *= self._boost_for(rows[iid]["type"])

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        chunks = []
        for iid, boosted in ranked:
            r = rows[iid]
            src = sources[iid]
            source = "hybrid" if len(src) == 2 else next(iter(src))
            chunks.append(RetrievedChunk(
                item_id=iid, title=r["title"], type=r["type"], content=r["content"],
                org_path=self.org_path(r["org_id"]), similarity=sim.get(iid),
                source=source, rrf_score=boosted,
            ))

        logger.debug(
            "retrieve(%r) → %d results | org_scope: %s | vec: %d | bm25: %d | rrf+boost: %d",
            query[:50], len(chunks), org_id or "all", len(sem), len(kw), len(chunks),
        )
        return chunks

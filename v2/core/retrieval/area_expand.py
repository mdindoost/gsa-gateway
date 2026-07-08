"""LLM-verified area expansion: umbrella research query -> all owned field experts.
Embeddings recall (KNN + token-overlap) -> LLM precision -> existing deterministic SQL. Fail-safe to exact."""
from __future__ import annotations
import hashlib, json, logging, os, sqlite3
import numpy as np
from v2.core.retrieval import area_cache
logger = logging.getLogger(__name__)


def area_vocab(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT je.value FROM knowledge_items k, json_each(k.metadata,'$.areas') je "
        "WHERE k.type='research_areas' AND k.is_active=1")
    seen, out = set(), []
    for (v,) in rows:
        v = (v or "").strip()
        if v and v.casefold() not in seen:
            seen.add(v.casefold()); out.append(v)
    return out


def vocab_signature(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(metadata)),0), COALESCE(MAX(id),0) "
        "FROM knowledge_items WHERE type='research_areas' AND is_active=1").fetchone()
    return hashlib.sha1(f"{row[0]}:{row[1]}:{row[2]}".encode()).hexdigest()[:16]


_VOCAB_MEMO: dict[str, tuple[list[str], "np.ndarray"]] = {}


def vocab_embeddings(conn: sqlite3.Connection, embedder=None):
    sig = vocab_signature(conn)
    if sig in _VOCAB_MEMO:
        return _VOCAB_MEMO[sig]
    from v2.core.retrieval import area_cache
    tags = area_vocab(conn)
    blob = area_cache.get_blob(f"vocab:{sig}")
    mat = None
    if blob is not None and tags:
        try:
            flat = np.frombuffer(blob, dtype=np.float32)
            if flat.size and flat.size % len(tags) == 0:
                mat = flat.reshape(len(tags), -1)
        except ValueError:
            mat = None
    if mat is None:
        if embedder is None:
            from v2.core.retrieval.embedder import Embedder
            embedder = Embedder()
        vecs = embedder.embed_documents(tags)
        if not any(vecs):
            raise RuntimeError("area vocab embedding returned no vectors")
        dim = len(next(v for v in vecs if v))
        mat = np.array([v if v else [0.0] * dim for v in vecs], dtype=np.float32)
        area_cache.put_blob(f"vocab:{sig}", mat.tobytes())
    _VOCAB_MEMO[sig] = (tags, mat)
    return tags, mat


_STOP = {"and", "or", "of", "the", "in", "for", "with", "a", "an", "to", "on", "research", "area", "areas"}


def _tokens(s: str) -> set[str]:
    import re
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").casefold()) if t not in _STOP and len(t) > 2}


def candidate_tags(conn: sqlite3.Connection, area: str, k: int = 30, embedder=None) -> list[str]:
    """Recall-only candidate shortlist for LLM-verified area expansion: union of (a) cosine
    top-k over the vocab embeddings and (b) every vocab tag sharing a non-stopword token with
    `area`. Loose on purpose — precision is a later LLM step, over-including here is correct."""
    from v2.core.retrieval.skills import expand_area
    terms = expand_area(area) or [area]            # R10: canonicalize ml->machine learning etc.
    tags, mat = vocab_embeddings(conn, embedder=embedder)
    if not tags:
        return []
    if embedder is None:
        from v2.core.retrieval.embedder import Embedder
        embedder = Embedder()
    out: set[str] = set()
    # (a) KNN over the canonical query form(s)
    for term in terms:
        q = embedder.embed_query(term)
        if q:
            sims = mat @ np.array(q, dtype=np.float32)
            for idx in np.argsort(-sims)[:k]:
                out.add(tags[idx])
    # (b) token-overlap recall channel (R5) — deterministic, LLM prunes precision
    qtok = set().union(*[_tokens(t) for t in terms]) if terms else _tokens(area)
    for t in tags:
        if _tokens(t) & qtok:
            out.add(t)
    return sorted(out)


PROMPT_VERSION = "v3"
VERIFY_MODEL = os.getenv("AREA_VERIFY_MODEL", "gemma3:12b")
_VERIFY_SCHEMA = {"type": "object", "properties": {"indices": {"type": "array", "items": {"type": "integer"}}},
                  "required": ["indices"]}
_SYSTEM = (
    "You are a STRICT research-field classifier. Given a QUERY field and a numbered list of research-area "
    "TAGS, return ONLY the tag numbers that name the SAME field as the query: the query field itself, a "
    "synonym, or a genuine sub-area of it. Be strict and EXCLUDE when unsure. Two rules: (1) sharing a WORD "
    "does not make a tag the same field — 'neural networks' is machine learning, not 'computer networks'; "
    "'human machine systems' is not 'machine learning'; 'Computer Science' is broader than 'computer "
    "networks'. (2) A broader PARENT field is not the same as a specific query — under 'recommender systems', "
    "'machine learning' does NOT belong. If no tag belongs, return an empty list.")
_FEWSHOT = (
    "FIELD: computer networks\nTAGS:\n1. wireless networks\n2. neural networks\n3. Computer Science\n"
    "4. datacenter networks\nAnswer: {\"indices\":[1,4]}\n"
    "FIELD: machine learning\nTAGS:\n1. deep learning\n2. human machine systems\n3. motor learning\n"
    "Answer: {\"indices\":[1]}\n"
    "FIELD: cyber security\nTAGS:\n1. network security\n2. machine learning\n3. cyber physical systems\n"
    "Answer: {\"indices\":[1]}\n"
    "FIELD: recommender systems\nTAGS:\n1. recommender systems\n2. machine learning\n"
    "Answer: {\"indices\":[1]}\n")


def _default_verify(system, prompt, schema):
    from bot.services.ollama_client import generate_json_sync
    return generate_json_sync(system, prompt, schema, model=VERIFY_MODEL, timeout=30.0)


VERIFY_CHUNK = int(os.getenv("AREA_VERIFY_CHUNK", "10"))


class VerifyError(Exception):
    """The verify backend errored (None/malformed response) — distinct from a valid empty pick.
    Raised so the orchestrator can fall back to exact-match WITHOUT caching (else a transient
    Ollama blip on a topic's first ask would freeze it at exact-match forever). See R8/I1."""


def _verify_one(area: str, candidates: list[str], verify) -> list[str]:
    """One verify call over a small candidate batch. Returns the belonging subset (order-preserved).
    Raises VerifyError when the backend returns None/malformed (a transport/model failure) — this is
    NOT the same as a valid `{"indices":[]}` (legitimately nothing belongs, returns [])."""
    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(candidates))
    prompt = f"{_FEWSHOT}\nFIELD: {area}\nTAGS:\n{listing}\nAnswer with the belonging tag numbers."
    res = verify(_SYSTEM, prompt, _VERIFY_SCHEMA)
    if not res or not isinstance(res.get("indices"), list):
        raise VerifyError(f"verify backend returned no usable result for {area!r}")
    picked = []
    for n in res["indices"]:
        if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(candidates):
            picked.append(candidates[n - 1])
    return picked


def llm_verify(area: str, candidates: list[str], verify=None) -> list[str]:
    """Precision step: ask the LLM which of `candidates` are the SAME research field as `area`.
    `verify` is an injected (system, prompt, schema) -> dict|None callable (tests stub it with no
    Ollama); defaults to a generate_json_sync partial bound to AREA_VERIFY_MODEL. Candidates are
    verified in SMALL CHUNKS (AREA_VERIFY_CHUNK, default 10): a long candidate list dilutes the judge
    (it over-includes), while it is near-perfect on short lists — so we chunk and union. Raises
    VerifyError if any chunk's backend errored (None/malformed) so the orchestrator can fall back
    WITHOUT caching a degraded result; a valid `{"indices":[]}` (nothing belongs) is NOT an error."""
    if not candidates:
        return []
    verify = verify or _default_verify
    picked: list[str] = []
    for i in range(0, len(candidates), VERIFY_CHUNK):
        picked.extend(_verify_one(area, candidates[i:i + VERIFY_CHUNK], verify))
    return list(dict.fromkeys(picked))


ENABLED = os.getenv("AREA_EXPAND_ENABLED", "1") == "1"
TOP_K = int(os.getenv("AREA_EXPAND_K", "30"))


def expand_area_llm(conn: sqlite3.Connection, area: str, embedder=None, verify=None) -> set[str]:
    """Orchestrator (R3, R8): umbrella research-area query -> verified owned tag set.
    flag check -> cache lookup (key includes model+prompt-version+K+vocab-signature so a swap
    never serves a stale verification) -> candidate_tags -> llm_verify -> cache write -> return.
    Fail-safe: ANY exception (including a disabled/missing cache) logs a warning and returns
    an empty set so the caller falls back to exact-match, never raises."""
    if not ENABLED or not (area or "").strip():
        return set()
    try:
        sig = vocab_signature(conn)
        key = (f"{' '.join((area or '').lower().split())}|{sig}|{VERIFY_MODEL}"
               f"|{PROMPT_VERSION}|{TOP_K}|{VERIFY_CHUNK}")
        cached = area_cache.get(key)
        if cached is not None:
            logger.info("area_expand cache=hit area=%r n=%d", area, len(cached))
            return set(cached)
        cands = candidate_tags(conn, area, k=TOP_K, embedder=embedder)
        verified = llm_verify(area, cands, verify=verify)
        area_cache.put(key, verified)
        logger.info("area_expand cache=miss area=%r cands=%d verified=%d", area, len(cands), len(verified))
        return set(verified)
    except Exception as e:  # noqa: BLE001 - fail-safe to exact
        logger.warning("area_expand ERROR area=%r: %s -> fallback exact", area, e)
        return set()

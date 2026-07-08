# LLM-Verified Area Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Umbrella research-area queries surface all owned field experts (e.g. "who works on cyber security"
1→≥12) by expanding the queried field to our owned area tags via embeddings-recall + LLM-precision, feeding the
existing deterministic SQL.

**Architecture:** New pure-ish module `v2/core/retrieval/area_expand.py`: embed query → candidate tags
(KNN top-30 ∪ token-overlap) → LLM verify same-field subset → cached in OPS DB. The two *enumerate* skills call
an `expand=True` path; the per-person yes/no gains a `"related"` honest-partial verdict so it never contradicts
the expanded list. Fail-safe: any error → today's exact-phrase behavior.

**Tech Stack:** Python 3.11, sqlite3 (+ OPS DB), Ollama (`generate_json_sync` structured output +
`Embedder` Qwen3-Embedding-0.6B), pytest.

## Global Constraints
- **LLM-agnostic (HARD):** verify model read from env `AREA_VERIFY_MODEL` (default `llama3.1:8b`); embedder from
  `active_descriptor()`. No hardcoded model beyond the default.
- **Fail-safe, never fail-loud:** any embed/LLM/cache error → fall back to exact-phrase match (status quo).
- **Feature flag:** `AREA_EXPAND_ENABLED` (default `"1"`); off → exact behavior.
- **Graph-write invariant:** skills receive a caller-owned `conn` and MUST NOT write it. The cache uses its OWN
  short-lived writable OPS connection.
- **Anti-fabrication:** never assert an unlisted attribute on a name. Expanded rosters are rendered under a
  "…-related areas" header with each name annotated by that person's OWN matched tag.
- **Determinism via cache:** verify runs at temp 0; the OPS cache (keyed incl. model+prompt+K) delivers stability.
- **TDD:** failing test → minimal impl → pass → commit. Unit tests inject stub embedder/verify — no Ollama.

---

### Task 1: OPS-DB cache tables + `area_cache.py` accessor

**Files:**
- Modify: `v2/core/database/schema.py` (add 2 DDL consts; append to `_OPS_TABLE_DDL` ~line 572)
- Create: `v2/core/retrieval/area_cache.py`
- Test: `v2/tests/test_area_cache.py`

**Interfaces:**
- Produces: `area_cache.get(key: str) -> list[str] | None`, `area_cache.put(key: str, tags: list[str]) -> None`,
  `area_cache.get_blob(name: str) -> bytes | None`, `area_cache.put_blob(name: str, data: bytes) -> None`.
  All open their own writable OPS conn (from `OPERATIONS_DB_PATH` / sibling of `DATABASE_PATH`), commit, close.
  Never raise (log + no-op on error) — cache is best-effort.

- [ ] **Step 1: Write the failing test**
```python
# v2/tests/test_area_cache.py
import os, importlib
def test_put_get_roundtrip_and_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "kb.db"))
    from v2.core.database import schema
    schema.create_ops_schema(str(tmp_path / "ops.db"))          # tables exist
    import v2.core.retrieval.area_cache as ac; importlib.reload(ac)
    assert ac.get("k1") is None
    ac.put("k1", ["cyber security", "network security"])
    assert ac.get("k1") == ["cyber security", "network security"]
    assert ac.get_blob("vocab") is None
    ac.put_blob("vocab", b"\x00\x01\x02")
    assert ac.get_blob("vocab") == b"\x00\x01\x02"
```

- [ ] **Step 2: Run test to verify it fails** — `pytest v2/tests/test_area_cache.py -v` → FAIL (module/tables absent).

- [ ] **Step 3: Implement.** In `schema.py`, add and register DDL (append to `_OPS_TABLE_DDL`):
```python
AREA_EXPAND_CACHE = """
CREATE TABLE IF NOT EXISTS area_expand_cache (
    key TEXT PRIMARY KEY,          -- normalized_area | vocab_hash | model | prompt_ver | k
    tags_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
) STRICT;
"""
AREA_VOCAB_BLOB = """
CREATE TABLE IF NOT EXISTS area_vocab_blob (
    name TEXT PRIMARY KEY,         -- e.g. 'vocab:<vocab_hash>'
    data BLOB NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
) STRICT;
"""
```
Create `area_cache.py`:
```python
"""Best-effort persistent cache for LLM-verified area expansion (lives in the OPS DB, NOT the KB DB).
Opens its OWN short-lived writable connection so the read-path skills never write their caller-owned conn."""
from __future__ import annotations
import json, logging, os, sqlite3
from pathlib import Path
from v2.core.database.schema import get_ops_connection, create_ops_schema
logger = logging.getLogger(__name__)

def _ops_path() -> str:
    p = os.getenv("OPERATIONS_DB_PATH")
    if p:
        return p
    db = os.getenv("DATABASE_PATH", "./gsa_gateway.db")
    return str(Path(db).parent / "gsa_gateway_ops.db")

def _conn() -> sqlite3.Connection:
    return create_ops_schema(_ops_path())     # idempotent; ensures tables + WAL

def get(key: str) -> list[str] | None:
    try:
        c = _conn()
        row = c.execute("SELECT tags_json FROM area_expand_cache WHERE key=?", (key,)).fetchone()
        c.close()
        return json.loads(row[0]) if row else None
    except Exception as e:  # noqa: BLE001 - best-effort
        logger.warning("area_cache.get failed: %s", e); return None

def put(key: str, tags: list[str]) -> None:
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO area_expand_cache(key, tags_json) VALUES (?,?)",
                  (key, json.dumps(tags)))
        c.commit(); c.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.put failed: %s", e)

def get_blob(name: str) -> bytes | None:
    try:
        c = _conn()
        row = c.execute("SELECT data FROM area_vocab_blob WHERE name=?", (name,)).fetchone()
        c.close()
        return bytes(row[0]) if row else None
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.get_blob failed: %s", e); return None

def put_blob(name: str, data: bytes) -> None:
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO area_vocab_blob(name, data) VALUES (?,?)", (name, data))
        c.commit(); c.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("area_cache.put_blob failed: %s", e)
```

- [ ] **Step 4: Run test to verify it passes** — `pytest v2/tests/test_area_cache.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/database/schema.py v2/core/retrieval/area_cache.py v2/tests/test_area_cache.py
git commit -m "feat(area-expansion): OPS-DB cache tables + best-effort area_cache accessor"
```

---

### Task 2: `Embedder.embed_documents` (batched, prefixed, normalized)

**Files:**
- Modify: `v2/core/retrieval/embedder.py` (add method after `embed_document`, ~line 76)
- Test: `v2/tests/test_embedder_batch.py`

**Interfaces:**
- Produces: `Embedder.embed_documents(texts: list[str]) -> list[list[float] | None]` — one batched `/api/embed`
  call, each text passed through the document prefix + truncation, each result L2-normalized. Aligns 1:1 with input.

- [ ] **Step 1: Write the failing test** (stubs the HTTP layer via `_embed_batch`):
```python
# v2/tests/test_embedder_batch.py
from v2.core.retrieval.embedder import Embedder
def test_embed_documents_prefixes_and_normalizes(monkeypatch):
    e = Embedder.__new__(Embedder)
    from v2.core.retrieval.model_descriptor import active_descriptor
    e.descriptor = active_descriptor()
    seen = {}
    def fake_batch(texts, timeout=60):
        seen["texts"] = texts
        return [[3.0, 4.0], [0.0, 0.0]]        # 2nd is un-normalizable
    e._embed_batch = fake_batch
    out = e.embed_documents(["alpha", "beta"])
    assert seen["texts"][0].endswith("alpha")   # doc prefix applied, text preserved
    assert abs((out[0][0]**2 + out[0][1]**2) - 1.0) < 1e-6   # normalized
    assert out[1] is None                        # zero vector → None
```

- [ ] **Step 2: Run test to verify it fails** — `pytest v2/tests/test_embedder_batch.py -v` → FAIL (no method).

- [ ] **Step 3: Implement** in `embedder.py`:
```python
    def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        """Batch-embed passages (doc prefix + truncate + L2-normalize). For the area-tag vocabulary."""
        prepared = [self._prepare(self.descriptor.doc_prefix, t) for t in texts]
        return [self.normalize(v) for v in self._embed_batch(prepared)]
```

- [ ] **Step 4: Run test to verify it passes** — `pytest v2/tests/test_embedder_batch.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/embedder.py v2/tests/test_embedder_batch.py
git commit -m "feat(area-expansion): Embedder.embed_documents batched doc-prefixed normalized embed"
```

---

### Task 3: area vocabulary + vocab hash + cached vocab embeddings

**Files:**
- Create: `v2/core/retrieval/area_expand.py` (start it here; grows in Tasks 4-6)
- Test: `v2/tests/test_area_vocab.py`

**Interfaces:**
- Consumes: `area_cache` (Task 1), `Embedder.embed_documents` (Task 2).
- Produces:
  - `area_vocab(conn) -> list[str]` — distinct active area-tag values (from `research_areas` items' `metadata.areas`).
  - `vocab_signature(conn) -> str` — cheap change-detector: `sha1(f"{count}:{max_rowid}")` over active
    `research_areas` items. Recomputed per call (cheap); full vocab list + embeddings rebuilt only when it changes.
  - `vocab_embeddings(conn, embedder=None) -> tuple[list[str], "np.ndarray"]` — the vocab and an (N, dim) float32
    matrix (L2-normalized rows), memoized in-process keyed by signature, persisted to `area_cache` blob.

- [ ] **Step 1: Write the failing test** (fixture DB with a few research_areas items; stub embedder):
```python
# v2/tests/test_area_vocab.py
import numpy as np, sqlite3, json
import v2.core.retrieval.area_expand as ax
def _fixture(conn):
    conn.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, type TEXT, is_active INT, metadata TEXT)")
    for i,(tags) in enumerate([["cyber security"],["network security","cloud security"]]):
        conn.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                     (json.dumps({"entity_id": f"e{i}", "areas": tags}),))
    conn.commit()
def test_vocab_and_signature_and_embeddings(monkeypatch):
    conn = sqlite3.connect(":memory:"); _fixture(conn)
    assert set(ax.area_vocab(conn)) == {"cyber security","network security","cloud security"}
    sig1 = ax.vocab_signature(conn)
    class Stub:  # deterministic 2-d embeddings
        def embed_documents(self, texts): return [[1.0,0.0] for _ in texts]
    tags, mat = ax.vocab_embeddings(conn, embedder=Stub())
    assert len(tags) == 3 and mat.shape == (3,2)
    conn.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                 (json.dumps({"entity_id":"e9","areas":["malware"]}),)); conn.commit()
    assert ax.vocab_signature(conn) != sig1        # change detected
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_vocab.py -v` → FAIL (module absent).

- [ ] **Step 3: Implement** the top of `area_expand.py`:
```python
"""LLM-verified area expansion: umbrella research query -> all owned field experts.
Embeddings recall (KNN + token-overlap) -> LLM precision -> existing deterministic SQL. Fail-safe to exact."""
from __future__ import annotations
import hashlib, json, logging, os, sqlite3
import numpy as np
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
        "SELECT COUNT(*), COALESCE(MAX(id),0) FROM knowledge_items "
        "WHERE type='research_areas' AND is_active=1").fetchone()
    return hashlib.sha1(f"{row[0]}:{row[1]}".encode()).hexdigest()[:16]

_VOCAB_MEMO: dict[str, tuple[list[str], "np.ndarray"]] = {}

def vocab_embeddings(conn: sqlite3.Connection, embedder=None):
    sig = vocab_signature(conn)
    if sig in _VOCAB_MEMO:
        return _VOCAB_MEMO[sig]
    from v2.core.retrieval import area_cache
    tags = area_vocab(conn)
    blob = area_cache.get_blob(f"vocab:{sig}")
    if blob is not None:
        mat = np.frombuffer(blob, dtype=np.float32).reshape(len(tags), -1)
    else:
        if embedder is None:
            from v2.core.retrieval.embedder import Embedder
            embedder = Embedder()
        vecs = embedder.embed_documents(tags)
        dim = len(next(v for v in vecs if v))
        mat = np.array([v if v else [0.0]*dim for v in vecs], dtype=np.float32)
        area_cache.put_blob(f"vocab:{sig}", mat.tobytes())
    _VOCAB_MEMO[sig] = (tags, mat)
    return tags, mat
```

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_vocab.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/area_expand.py v2/tests/test_area_vocab.py
git commit -m "feat(area-expansion): area vocabulary + change-detector signature + cached vocab embeddings"
```

---

### Task 4: candidate shortlist — KNN ∪ token-overlap (R5)

**Files:**
- Modify: `v2/core/retrieval/area_expand.py`
- Test: `v2/tests/test_area_candidates.py`

**Interfaces:**
- Consumes: `vocab_embeddings` (Task 3), `Embedder.embed_query`.
- Produces: `candidate_tags(conn, area, k=30, embedder=None) -> list[str]` — union of (a) cosine top-k over vocab,
  (b) every vocab tag sharing a non-stopword token with `area`. Query canonicalized via `expand_area` first (R10).

- [ ] **Step 1: Write the failing test** (stub embedder gives "security"-ish vectors close; token-overlap covers the rest):
```python
# v2/tests/test_area_candidates.py
import sqlite3, json
import v2.core.retrieval.area_expand as ax
def _fx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,type TEXT,is_active INT,metadata TEXT)")
    for i,t in enumerate(["cyber security","network security","machine learning"]):
        c.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                  (json.dumps({"entity_id":f"e{i}","areas":[t]}),))
    c.commit(); return c
class Stub:
    def embed_documents(self, texts): return [[1.0,0.0] if "secur" in t else [0.0,1.0] for t in texts]
    def embed_query(self, t): return [1.0,0.0]        # near the security vectors
def test_candidates_union(monkeypatch):
    conn = _fx()
    cands = ax.candidate_tags(conn, "cyber security", k=1, embedder=Stub())
    # token-overlap guarantees BOTH security tags regardless of k=1 KNN
    assert "network security" in cands and "cyber security" in cands
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_candidates.py -v` → FAIL.

- [ ] **Step 3: Implement** in `area_expand.py`:
```python
_STOP = {"and","or","of","the","in","for","with","a","an","to","on","research","area","areas"}

def _tokens(s: str) -> set[str]:
    import re
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").casefold()) if t not in _STOP and len(t) > 2}

def candidate_tags(conn, area: str, k: int = 30, embedder=None) -> list[str]:
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
```

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_candidates.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/area_expand.py v2/tests/test_area_candidates.py
git commit -m "feat(area-expansion): candidate shortlist = KNN top-k UNION token-overlap (recall channel)"
```

---

### Task 5: `llm_verify` — same-field subset via generate_json_sync (R6, R7)

**Files:**
- Modify: `v2/core/retrieval/area_expand.py`
- Test: `v2/tests/test_area_verify.py`

**Interfaces:**
- Produces: `llm_verify(area, candidates, verify=None) -> list[str]` — returns the subset of `candidates` the LLM
  judges same-field. `verify` is an injected callable `(system, prompt, schema) -> dict | None` (defaults to a
  `generate_json_sync` partial bound to `AREA_VERIFY_MODEL`, timeout 20s). Defensive parse of `{"indices":[int]}`.
- Constant `PROMPT_VERSION = "v1"`.

- [ ] **Step 1: Write the failing test** (stub verify returns indices; also test directional-negative + garbage):
```python
# v2/tests/test_area_verify.py
import v2.core.retrieval.area_expand as ax
def test_verify_selects_and_is_defensive():
    cands = ["cyber security","network security","machine learning"]
    stub = lambda system, prompt, schema: {"indices":[0,1]}
    assert ax.llm_verify("cyber security", cands, verify=stub) == ["cyber security","network security"]
    assert ax.llm_verify("x", cands, verify=lambda *a: None) == []          # LLM error -> empty
    assert ax.llm_verify("x", cands, verify=lambda *a: {"indices":[99,-1]}) == []  # out-of-range ignored
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_verify.py -v` → FAIL.

- [ ] **Step 3: Implement** in `area_expand.py`:
```python
PROMPT_VERSION = "v1"
VERIFY_MODEL = os.getenv("AREA_VERIFY_MODEL", "llama3.1:8b")
_VERIFY_SCHEMA = {"type":"object","properties":{"indices":{"type":"array","items":{"type":"integer"}}},
                  "required":["indices"]}
_SYSTEM = (
 "You decide which specific research-area tags belong to the SAME research field as a query field. "
 "A tag belongs if a domain expert would file it under the query field (subfields and near-synonyms count). "
 "A merely RELATED but DISTINCT field does NOT belong. Answer ONLY with the tag numbers that belong.")
_FEWSHOT = (
 "Example — FIELD: security\nTAGS:\n1. network security\n2. neural networks\n3. cloud security\n"
 "Answer: {\"indices\":[1,3]}\n"
 "Example (directional) — FIELD: recommender systems\nTAGS:\n1. recommender systems\n2. machine learning\n"
 "Answer: {\"indices\":[1]}\n")   # parent field 'machine learning' REJECTED under the specific query

def _default_verify(system, prompt, schema):
    from bot.services.ollama_client import generate_json_sync
    return generate_json_sync(system, prompt, schema, model=VERIFY_MODEL, timeout=20.0)

def llm_verify(area: str, candidates: list[str], verify=None) -> list[str]:
    if not candidates:
        return []
    verify = verify or _default_verify
    listing = "\n".join(f"{i+1}. {t}" for i, t in enumerate(candidates))
    prompt = f"{_FEWSHOT}\nFIELD: {area}\nTAGS:\n{listing}\nAnswer with the belonging tag numbers."
    res = verify(_SYSTEM, prompt, _VERIFY_SCHEMA)
    if not res or not isinstance(res.get("indices"), list):
        return []
    picked = []
    for n in res["indices"]:
        if isinstance(n, int) and 1 <= n <= len(candidates):
            picked.append(candidates[n-1])
    return list(dict.fromkeys(picked))
```

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_verify.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/area_expand.py v2/tests/test_area_verify.py
git commit -m "feat(area-expansion): llm_verify (generate_json_sync + JSON schema + directional few-shot)"
```

---

### Task 6: `expand_area_llm` orchestrator — flag, cache, logging, fail-safe (R3, R8)

**Files:**
- Modify: `v2/core/retrieval/area_expand.py`
- Test: `v2/tests/test_area_expand_orchestrator.py`

**Interfaces:**
- Produces: `expand_area_llm(conn, area, embedder=None, verify=None) -> set[str]` — the verified owned tags for
  `area` (empty set on disabled/error/none). Orchestrates candidate→verify→cache. Cache key includes model +
  `PROMPT_VERSION` + K + `vocab_signature` (R3). Emits one structured log line (R8).

- [ ] **Step 1: Write the failing test:**
```python
# v2/tests/test_area_expand_orchestrator.py
import sqlite3, json, importlib
import v2.core.retrieval.area_expand as ax
def _fx():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,type TEXT,is_active INT,metadata TEXT)")
    for i,t in enumerate(["cyber security","network security"]):
        c.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                  (json.dumps({"entity_id":f"e{i}","areas":[t]}),))
    c.commit(); return c
class Stub:
    def embed_documents(self, texts): return [[1.0,0.0] for _ in texts]
    def embed_query(self, t): return [1.0,0.0]
def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED","0"); importlib.reload(ax)
    assert ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(), verify=lambda *a:{"indices":[1,2]}) == set()
def test_enabled_returns_verified(monkeypatch):
    monkeypatch.setenv("AREA_EXPAND_ENABLED","1"); importlib.reload(ax)
    monkeypatch.setattr(ax, "area_cache", __import__("types").SimpleNamespace(
        get=lambda k: None, put=lambda k,v: None, get_blob=lambda n: None, put_blob=lambda n,d: None))
    out = ax.expand_area_llm(_fx(), "cyber security", embedder=Stub(), verify=lambda *a:{"indices":[1,2]})
    assert out == {"cyber security","network security"}
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_expand_orchestrator.py -v` → FAIL.

- [ ] **Step 3: Implement** in `area_expand.py`:
```python
ENABLED = os.getenv("AREA_EXPAND_ENABLED", "1") == "1"
TOP_K = int(os.getenv("AREA_EXPAND_K", "30"))

def expand_area_llm(conn, area: str, embedder=None, verify=None) -> set[str]:
    if not ENABLED or not (area or "").strip():
        return set()
    from v2.core.retrieval import area_cache
    try:
        sig = vocab_signature(conn)
        key = f"{' '.join((area or '').lower().split())}|{sig}|{VERIFY_MODEL}|{PROMPT_VERSION}|{TOP_K}"
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
```

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_expand_orchestrator.py -v` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/area_expand.py v2/tests/test_area_expand_orchestrator.py
git commit -m "feat(area-expansion): expand_area_llm orchestrator (flag, complete cache key, logging, fail-safe)"
```

---

### Task 7: skills hook — expand path + `related` verdict (R1, R2)

**Files:**
- Modify: `v2/core/retrieval/skills.py` (`_research_entities` ~297; `people_by_research_area` 324;
  `count_people_by_research_area` 331; `does_person_research_area` 338; add `people_by_research_area_annotated`)
- Test: `v2/tests/test_area_expand_skills.py`

**Interfaces:**
- Consumes: `area_expand.expand_area_llm`, `_area_rows`, `_named_rows`.
- Produces:
  - `_research_entities(conn, area, org_id, expand=False)` — `expand=True` unions exact ∪ people who list any
    verified tag.
  - `people_by_research_area(conn, area, org_id)` → uses `expand=True`.
  - `people_by_research_area_annotated(conn, area, org_id) -> list[tuple[str,str,str]]` — `(name, entity_id,
    matched_tag)`; `matched_tag` = the query area for exact hits, else the person's verified sibling tag.
  - `count_people_by_research_area(conn, area, org_id)` → `len(_research_entities(expand=True))`.
  - `does_person_research_area(...)` gains verdict `"related"` (exact-no but holds a verified sibling tag).

- [ ] **Step 1: Write the failing tests** (real-ish fixture; stub the expansion via monkeypatch):
```python
# v2/tests/test_area_expand_skills.py
import sqlite3, json
import v2.core.retrieval.skills as sk
def _fx(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,type TEXT,is_active INT,title TEXT,metadata TEXT,content TEXT)")
    people = {"e_wu":["cyber security"], "e_neamtiu":["system security"], "e_ml":["machine learning"]}
    for eid, tags in people.items():
        c.execute("INSERT INTO knowledge_items(type,is_active,title,metadata,content) "
                  "VALUES('research_areas',1,?,?,?)",
                  (eid, json.dumps({"entity_id":eid,"areas":tags}), " ".join(tags)))
    c.commit()
    # stub expansion: cyber security -> {cyber security, system security}
    monkeypatch.setattr(sk, "_expand_llm",
        lambda conn, area: {"cyber security","system security"} if "security" in area else set())
    # stub name resolution to identity
    monkeypatch.setattr(sk, "_named_rows", lambda conn, ids: sorted((i.replace("e_","").title(), i) for i in ids))
    return c
def test_enumerate_expands_but_yesno_related(monkeypatch):
    c = _fx(monkeypatch)
    names = {n for n,_ in sk.people_by_research_area(c, "cyber security", None)}
    assert {"Wu","Neamtiu"} <= names and "Ml" not in names       # expanded, ML excluded
    assert sk.count_people_by_research_area(c, "cyber security", None) == len(names)
    # yes/no: Neamtiu is exact-NO for 'cyber security' but holds sibling 'system security' -> 'related', never 'no'
    r = sk.does_person_research_area(c, "e_neamtiu", "cyber security", "Neamtiu")
    assert r["answer"] == "related" and r["matched_area"] == "system security"
    # Wu lists it exactly -> yes
    assert sk.does_person_research_area(c, "e_wu", "cyber security", "Wu")["answer"] == "yes"
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_expand_skills.py -v` → FAIL.

- [ ] **Step 3: Implement** in `skills.py`. Add a thin seam + expand param:
```python
def _expand_llm(conn, area):                          # seam (monkeypatchable in tests)
    from v2.core.retrieval.area_expand import expand_area_llm
    return expand_area_llm(conn, area)

def _people_by_verified_tags(conn, verified, org_id):
    if not verified:
        return {}
    targets = {t.casefold() for t in verified}
    return {eid: val for val, eid in _area_rows(conn, org_id) if val.casefold() in targets}
```
Change `_research_entities` signature to `(conn, area, org_id, expand=False)`; at its end, when `expand`:
```python
    if expand:
        verified = _expand_llm(conn, area)
        result = result | set(_people_by_verified_tags(conn, verified, org_id).keys())
    return result
```
`people_by_research_area` → `_research_entities(conn, area, org_id, expand=True)`.
`count_people_by_research_area` → `len(_research_entities(conn, area, org_id, expand=True))`.
Add:
```python
def people_by_research_area_annotated(conn, area, org_id=None):
    exact = _research_entities(conn, area, org_id, expand=False)
    verified = _expand_llm(conn, area)
    sib = _people_by_verified_tags(conn, verified, org_id)      # eid -> a matched sibling tag
    eids = exact | set(sib.keys())
    named = dict(_named_rows(conn, list(eids)))                 # eid -> name (via existing helper mapping)
    out = []
    for eid in eids:
        tag = area if eid in exact else sib.get(eid, area)
        out.append((named.get(eid, eid), eid, tag))
    return sorted(out, key=lambda r: r[0].casefold())
```
In `does_person_research_area` (after computing `in_set` exact): if not `in_set`, compute
`related = entity_id in _people_by_verified_tags(conn, _expand_llm(conn, area), None)`; when `related`, set
`answer="related"`, `basis="related"`, `matched_area = <the person's verified sibling tag>` (look it up from
`_people_by_verified_tags` value). Keep existing `"yes"/"no"/"unknown"` otherwise.

*(Note: `_named_rows` returns `list[tuple[name, entity_id]]`; the annotated helper maps it to a dict. Verify the
real signature at implementation and adjust the `dict(...)` construction accordingly.)*

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_expand_skills.py -v` → PASS. Then run the FULL
  existing skills/area suite to prove no regression, esp. yes/no byte-identity for exact cases:
  `pytest v2/tests/ -k "skill or area or research" -q` → all PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/skills.py v2/tests/test_area_expand_skills.py
git commit -m "feat(area-expansion): skills expand=True path + does_person_research_area 'related' verdict"
```

---

### Task 8: answer rendering — transparent expanded wording (R4)

**Files:**
- Modify: `v2/core/retrieval/structured_answer.py` (`run` ~106; `format_answer` people_by_research_area ~529;
  does_person_research_area ~324)
- Test: `v2/tests/test_area_expand_render.py`

**Interfaces:**
- Consumes: `people_by_research_area_annotated`.
- `run` for `people_by_research_area` calls the annotated helper and passes `[(name, tag)]` into the result.
- `format_answer` renders: if any name's tag ≠ the queried area (expansion fired) →
  `"{n} faculty work in {area}-related areas: {Name (tag), …}."`; else the current
  `"{n} faculty work on \"{area}\": {names}."`. does_person_research_area gains the `"related"` branch:
  `"{name} lists {matched_area}, a form of {area} — I don't have \"{area}\" listed as such."`

- [ ] **Step 1: Write the failing test:**
```python
# v2/tests/test_area_expand_render.py
import v2.core.retrieval.structured_answer as sa
def test_expanded_roster_wording():
    result = {"skill":"people_by_research_area","area":"cyber security","org":None,
              "rows_annotated":[("Chase Wu","cyber security"),("Iulian Neamtiu","system security")]}
    txt = sa.format_answer(result)
    assert "security-related areas" in txt
    assert "Iulian Neamtiu (system security)" in txt
def test_related_verdict_wording():
    result = {"skill":"does_person_research_area",
              "answer":"related","name":"Iulian Neamtiu","area":"cyber security",
              "matched_area":"system security","person_areas":["system security"]}
    txt = sa.format_answer(result)
    assert "system security" in txt and "as such" in txt
```

- [ ] **Step 2: Run to verify fail** — `pytest v2/tests/test_area_expand_render.py -v` → FAIL.

- [ ] **Step 3: Implement.** In `run`, for `people_by_research_area` build `rows_annotated =
  [(n, tag) for n, _eid, tag in skills.people_by_research_area_annotated(conn, a["area"], a.get("org_id"))]`
  and keep `rows` = the names for back-compat. In `format_answer` add the expanded branch (detect any
  `tag != area`) and the `"related"` branch shown in the interface above. Preserve all existing wording when
  expansion didn't fire (rows_annotated all have `tag == area`).

- [ ] **Step 4: Run to verify pass** — `pytest v2/tests/test_area_expand_render.py -v` → PASS; re-run the
  structured_answer suite: `pytest v2/tests/ -k structured -q` → PASS.

- [ ] **Step 5: Commit** —
```bash
git add v2/core/retrieval/structured_answer.py v2/tests/test_area_expand_render.py
git commit -m "feat(area-expansion): transparent expanded-roster wording + related-verdict rendering"
```

---

### Task 9: live wiring + config + prewarm (R11) and end-to-end verification

**Files:**
- Modify: `bot/config.py` (surface `AREA_EXPAND_ENABLED`, `AREA_VERIFY_MODEL` if a config object is preferred over
  bare `os.getenv` — otherwise no change, env is read in `area_expand`).
- Modify: `scripts/restart.sh` OR bot startup (optional `keep_alive` prewarm of `AREA_VERIFY_MODEL`).
- Test: manual X-ray via `scripts/ask.sh`.

- [ ] **Step 1** — Confirm the skill path reaches expansion live (no code if env-only): run
  `bash scripts/ask.sh "who is working on cyber security" --answer`. Expected: **≥12 faculty**, rendered as
  "…security-related areas: … Iulian Neamtiu (system security) …", `used_ai` unchanged (structured path).
- [ ] **Step 2** — Precision spot-checks (must NOT over-match):
  `bash scripts/ask.sh "who works on computer networks" --answer` → excludes neural/social-network-only people;
  `bash scripts/ask.sh "who works on machine learning"` → excludes motor-/service-learning.
- [ ] **Step 3** — Follow-up contradiction check (R2):
  `does Iulian Neamtiu work on cyber security?` → a `"related"` honest-partial answer, NOT "No".
- [ ] **Step 4** — Kill-switch: `AREA_EXPAND_ENABLED=0 bash scripts/ask.sh "who is working on cyber security"`
  → back to 1 (Chase Wu). Confirms fail-safe.
- [ ] **Step 5: Commit** any wiring/prewarm changes —
```bash
git add -p && git commit -m "feat(area-expansion): live wiring, AREA_EXPAND_ENABLED/AREA_VERIFY_MODEL, verify prewarm"
```

---

### Task 10: verify-model gold gate + eval questions (R7, grow-correctness-suite)

**Files:**
- Create: `eval/area_expand/gold_pairs.jsonl` (~50 `{query, tag, belongs}` incl. measured traps)
- Create: `scripts/eval_area_verify.py` (scores `llm_verify` per-pair; prints precision/recall; exit 1 if
  precision < 0.9)
- Modify: `eval/questions.txt` (add the umbrella + precision + follow-up questions)

**Interfaces:** standalone script; no production dependency.

- [ ] **Step 1: Write the gold set** — ~50 pairs. MUST include the measured traps as negatives:
  `{"query":"computer networks","tag":"neural networks","belongs":false}`,
  `{"query":"machine learning","tag":"service-learning connections to writing","belongs":false}`,
  `{"query":"machine learning","tag":"motor learning","belongs":false}`,
  `{"query":"recommender systems","tag":"machine learning","belongs":false}` (directional),
  and positives: `{"query":"cyber security","tag":"network security","belongs":true}`,
  `{"query":"operating systems","tag":"distributed systems","belongs":true}`, etc.
- [ ] **Step 2: Write `scripts/eval_area_verify.py`** — for each pair call
  `area_expand.llm_verify(query, [tag])`; predicted `belongs = tag in result`; tally TP/FP/FN; print
  precision/recall; `sys.exit(1 if precision < 0.9 else 0)`.
- [ ] **Step 3: Run the gate** — `python scripts/eval_area_verify.py` with `AREA_VERIFY_MODEL=llama3.1:8b`.
  Expected: **precision ≥ 0.9**. If it fails, that is the signal to change `AREA_VERIFY_MODEL` (LLM-agnostic) —
  do NOT flip the flag on in production until it passes.
- [ ] **Step 4: Add eval questions** to `eval/questions.txt` under a `# research-area expansion` header:
  "who is working on cyber security", "who works on machine learning", "who works on computer networks",
  "does Iulian Neamtiu work on cyber security".
- [ ] **Step 5: Commit** —
```bash
git add eval/area_expand/gold_pairs.jsonl scripts/eval_area_verify.py eval/questions.txt
git commit -m "test(area-expansion): verify-model gold-set precision gate + eval questions"
```

---

## Self-Review (done)
- **Spec coverage:** R1(T7)·R2(T7,T8)·R3(T6)·R4(T8)·R5(T4)·R6(T5)·R7(T5,T10)·R8(T1,T6)·R9(T1)·R10(T4)·
  R11(T9)·flag(T6,T9)·gold-gate(T10)·eval Qs(T10). R12 deferred (documented in spec §8). All covered.
- **Placeholders:** the one soft spot is T7 Step 3's `_named_rows` mapping note — flagged for the implementer to
  match the real signature (verified `list[tuple[name, entity_id]]` at skills.py `_named_rows`). No TODOs.
- **Type consistency:** `expand_area_llm`→`set[str]`; `candidate_tags`/`llm_verify`→`list[str]`;
  `people_by_research_area_annotated`→`list[tuple[str,str,str]]`; verdict `"related"` string used in T7/T8.
  Cache key string identical across T6. Consistent.

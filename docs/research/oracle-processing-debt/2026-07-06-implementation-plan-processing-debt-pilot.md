# Processing-Debt Pilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, ~$3, ~50-question measurement instrument that classifies each material fact in a Brave-oracle answer as *in-our-answer / owned-but-not-surfaced / not-owned*, attributes each owned-miss to a pipeline stage, and validates itself against 100% human adjudication (Cohen's κ).

**Architecture:** A new self-contained package `eval/processing_debt/` of small, single-responsibility modules. Per question: fetch a Brave Answers oracle answer (cached) → decompose to atomic facts with vital/okay materiality → drop facts the oracle's own citation can't support → for each surviving vital fact, test entailment against our production answer (IN_ANSWER), else run an **exhaustive non-production presence check** (4 union probes over the DB) → if owned, attribute the miss to router/pool/rank/compose/config via the live retriever + an eRAG per-chunk utility check. All LLM calls use the existing local Ollama `generate_json_sync` (Granite structured JSON). Nothing writes to the DB.

**Tech Stack:** Python 3.11, SQLite + sqlite-vec (`gsa_gateway.db`), local Ollama (`generate_json_sync`, `Embedder`), the existing `V2Retriever`/`route`/`CrossEncoderReranker`, Brave Answers HTTP API, pytest.

## Global Constraints

- **Read-only against `gsa_gateway.db`.** No `INSERT`/`UPDATE`/`DELETE`; no writes to KB/KG. (Enforced: open the connection, never call a write helper.)
- **No production code paths modified.** This package only *imports* production modules; it never edits `bot/` or `v2/core/`. Findings go to a backlog, not to fixes (repo hard gate).
- **Oracle spend cap:** ≤ 50 Brave Answers queries total for the pilot; every oracle response cached to disk (`eval/processing_debt/.cache/oracle/`) so re-runs cost $0. Brave key = `BRAVE_ANSWERS_API_KEY` from `.env`; endpoint `POST https://api.search.brave.com/res/v1/chat/completions`, model `brave-pro`, OpenAI-compatible body `{"stream":false,"messages":[{"role":"user","content":q}]}`, header `X-Subscription-Token`.
- **No personal data outbound.** Guard page-fetches use a fixed project User-Agent string `GSA-Gateway-Research/1.0`; never send user emails/PII in any request.
- **LLM model for judging:** `generate_json_sync(..., model="granite4:tiny-h", temperature=0.0)` (the repo's structured-JSON default). Deterministic.
- **Repo import pattern:** every test and script inserts the repo root on `sys.path` (`REPO = Path(__file__).resolve().parents[N]; sys.path.insert(0, str(REPO))`).
- **DB access:** `from v2.core.database.schema import get_connection; conn = get_connection("gsa_gateway.db")`.
- **Materiality rule:** only `vital` facts count toward the Processing-Debt denominator; `okay` facts are recorded but excluded from the headline metric (verbosity-bias fix).
- **Deviation from approved spec (needs owner OK):** the spec's `ragchecker_adapter.py` (REUSE RAGChecker) is implemented here as a **local** `nuggetize.py` (decompose+materiality) + `entailment.py` (entails) using `generate_json_sync`, following RAGChecker/AutoNuggetizer *protocol* without the pip dependency. Interface stays swappable for the deferred scale phase.

---

## File Structure

New package `eval/processing_debt/` (all files created unless noted):

| File | Responsibility |
|---|---|
| `__init__.py` | package marker |
| `types.py` | frozen dataclasses shared across modules |
| `dbconn.py` | one read-only `get_ro_connection()` helper |
| `oracle_brave.py` | Brave Answers client + disk cache |
| `entailment.py` | `entails(fact, text) -> bool` (Granite structured JSON) — shared judge |
| `nuggetize.py` | decompose an oracle answer → atomic facts + vital/okay |
| `oracle_guard.py` | fetch cited page, NLI-support check, WE_ARE_AUTHORITY flag |
| `presence_check.py` | **the crux** — 4 union probes (kg/fts/embed/grep) + entailment |
| `xray.py` | run the live router+retriever → structured `XRay` (no stdout parsing) |
| `erag_attrib.py` | per-chunk utility (feed one chunk alone through compose) |
| `attribute.py` | decision tree → stage |
| `classify.py` | orchestrate one fact end-to-end → `FactRecord` |
| `sample.py` | stratified 50-question sampler + pipeline-path labeler |
| `adjudicate.py` | emit human-label CSV, ingest labels, compute Cohen's κ |
| `run_pilot.py` | driver over the sample → `facts.jsonl` |
| `report.py` | aggregate → `pilot_report.md` + SC1–SC6 pass/fail |

Tests live in `v2/tests/processing_debt/` (mirrors the repo's pytest home). Fixtures build a tiny standalone SQLite DB so probes are tested hermetically.

---

## Task 1: Package scaffold + `types.py` + read-only connection

**Files:**
- Create: `eval/processing_debt/__init__.py`, `eval/processing_debt/types.py`, `eval/processing_debt/dbconn.py`
- Create: `v2/tests/processing_debt/__init__.py`, `v2/tests/processing_debt/test_types.py`

**Interfaces:**
- Produces: dataclasses `OracleCitation, OracleAnswer, Nugget, GuardVerdict, PresenceEvidence, PresenceResult, XRay, Attribution, FactRecord`; `get_ro_connection(db_path="gsa_gateway.db") -> sqlite3.Connection`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_types.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from eval.processing_debt.types import Nugget, PresenceResult, FactRecord

def test_nugget_defaults():
    n = Nugget(text="Pan Xu is an Assistant Professor.", vital=True)
    assert n.vital is True and n.text.startswith("Pan Xu")

def test_presence_result_absent_default():
    p = PresenceResult(present=False, probes_hit=[], evidence=[])
    assert p.present is False and p.probes_hit == []

def test_factrecord_roundtrips_to_dict():
    fr = FactRecord(question="q", stratum="rag", fact_text="f", vital=True,
                    guard_verdict="supported", in_answer=False,
                    presence=PresenceResult(True, ["fts_probe"], []),
                    fact_class="OWNED_NOT_SURFACED", stage="POOL", xray_ref="q")
    d = fr.as_dict()
    assert d["fact_class"] == "OWNED_NOT_SURFACED" and d["presence"]["present"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.processing_debt.types'`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/__init__.py
```
```python
# v2/tests/processing_debt/__init__.py
```
```python
# eval/processing_debt/types.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class OracleCitation:
    url: str
    title: str | None = None

@dataclass
class OracleAnswer:
    question: str
    answer: str
    citations: list[OracleCitation] = field(default_factory=list)
    raw: dict | None = None

@dataclass
class Nugget:
    text: str
    vital: bool

@dataclass
class GuardVerdict:
    verdict: str          # 'supported' | 'unsupported' | 'we_are_authority'
    cited_url: str | None = None
    evidence_span: str | None = None

@dataclass
class PresenceEvidence:
    source_type: str      # 'node' | 'knowledge_item'
    row_or_node_id: str
    span: str
    probe: str            # 'kg_probe' | 'fts_probe' | 'embed_probe' | 'grep_probe'
    item_type: str | None = None   # knowledge_items.type, when applicable (e.g. 'publication')

@dataclass
class PresenceResult:
    present: bool
    probes_hit: list[str]
    evidence: list[PresenceEvidence]

@dataclass
class XRay:
    question: str
    router_family: str | None
    router_skill: str | None
    fused_pool_ids: list[int]
    top5_ids: list[int]
    ce_scores: dict[int, float]        # item_id -> ce_score (reranked pool)
    tier_primary_miss: bool
    answer: str | None

@dataclass
class Attribution:
    stage: str            # ROUTER|POOL|RANK|COMPOSE|CONFIG|UNRESOLVED
    reason: str

@dataclass
class FactRecord:
    question: str
    stratum: str
    fact_text: str
    vital: bool
    guard_verdict: str
    in_answer: bool
    presence: PresenceResult
    fact_class: str        # IN_ANSWER | OWNED_NOT_SURFACED | NOT_OWNED | DROPPED_ORACLE
    stage: str | None
    xray_ref: str
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
```
```python
# eval/processing_debt/dbconn.py
from __future__ import annotations
import sqlite3
from v2.core.database.schema import get_connection

def get_ro_connection(db_path: str = "gsa_gateway.db") -> sqlite3.Connection:
    """Read-only-by-discipline connection to the live DB. We never call write helpers.
    A row_factory gives dict-ish access."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_types.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/__init__.py eval/processing_debt/types.py eval/processing_debt/dbconn.py v2/tests/processing_debt/
git commit -m "feat(processing-debt): package scaffold, shared types, ro connection"
```

---

## Task 2: `oracle_brave.py` — Brave Answers client with disk cache

**Files:**
- Create: `eval/processing_debt/oracle_brave.py`
- Test: `v2/tests/processing_debt/test_oracle_brave.py`

**Interfaces:**
- Consumes: `OracleAnswer, OracleCitation` from `types`.
- Produces: `ask_oracle(question, *, cache_dir=".cache/oracle", http=None) -> OracleAnswer`. `http` is an injectable callable `(url, body_bytes, headers) -> dict` (default real urllib) so tests never spend money.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_oracle_brave.py
import sys, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.oracle_brave import ask_oracle

def _fake_http_factory(counter):
    def _http(url, body, headers):
        counter.append(1)
        return {"choices": [{"message": {"content": "Pan Xu is an Assistant Professor. [1]"}}],
                "citations": [{"url": "https://cs.njit.edu/pan-xu", "title": "Pan Xu"}]}
    return _http

def test_ask_oracle_parses_and_caches(tmp_path):
    calls = []
    oa = ask_oracle("who is pan xu", cache_dir=str(tmp_path), http=_fake_http_factory(calls))
    assert oa.answer.startswith("Pan Xu is an Assistant Professor")
    assert oa.citations[0].url == "https://cs.njit.edu/pan-xu"
    assert len(calls) == 1
    # second call hits cache — no new http
    oa2 = ask_oracle("who is pan xu", cache_dir=str(tmp_path), http=_fake_http_factory(calls))
    assert oa2.answer == oa.answer
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_oracle_brave.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/oracle_brave.py
from __future__ import annotations
import hashlib, json, os, urllib.request
from pathlib import Path
from eval.processing_debt.types import OracleAnswer, OracleCitation

ENDPOINT = "https://api.search.brave.com/res/v1/chat/completions"

def _read_key() -> str:
    for line in open(Path(__file__).resolve().parents[2] / ".env"):
        if line.startswith("BRAVE_ANSWERS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("BRAVE_ANSWERS_API_KEY not found in .env")

def _real_http(url: str, body: bytes, headers: dict) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _parse(question: str, data: dict) -> OracleAnswer:
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
    cites = [OracleCitation(url=c.get("url"), title=c.get("title"))
             for c in (data.get("citations") or []) if c.get("url")]
    return OracleAnswer(question=question, answer=content.strip(), citations=cites, raw=data)

def ask_oracle(question: str, *, cache_dir: str = "eval/processing_debt/.cache/oracle", http=None) -> OracleAnswer:
    http = http or _real_http
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
    cache_file = Path(cache_dir) / f"{key}.json"
    if cache_file.exists():
        return _parse(question, json.loads(cache_file.read_text()))
    body = json.dumps({"stream": False, "messages": [{"role": "user", "content": question}]}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "X-Subscription-Token": _read_key()}
    data = http(ENDPOINT, body, headers)
    cache_file.write_text(json.dumps(data))
    return _parse(question, data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_oracle_brave.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/oracle_brave.py v2/tests/processing_debt/test_oracle_brave.py
git commit -m "feat(processing-debt): cached Brave Answers oracle client"
```

> **Note on Brave's real response shape:** the live `brave-pro` response nests the answer at `choices[0].message.content`; the `citations` array shape is confirmed via a one-off live probe during Task 12 (integration). If live citations come back under a different key, adjust `_parse` only — the test's fake mirrors the documented shape.

---

## Task 3: `entailment.py` — the shared Granite entailment judge

**Files:**
- Create: `eval/processing_debt/entailment.py`
- Test: `v2/tests/processing_debt/test_entailment.py`

**Interfaces:**
- Produces: `entails(fact: str, text: str, *, gen=None) -> bool`. `gen` is an injectable `(system, prompt, schema) -> dict|None` (default wraps `generate_json_sync`) so tests are deterministic and offline.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_entailment.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.entailment import entails

def test_entails_true_when_model_says_supported():
    gen = lambda system, prompt, schema: {"supported": True}
    assert entails("Pan Xu is an Assistant Professor.", "Pan Xu, Assistant Professor of CS.", gen=gen) is True

def test_entails_false_when_model_says_unsupported():
    gen = lambda system, prompt, schema: {"supported": False}
    assert entails("Pan Xu won a Nobel Prize.", "Pan Xu, Assistant Professor.", gen=gen) is False

def test_entails_false_on_model_failure():
    gen = lambda system, prompt, schema: None
    assert entails("x", "y", gen=gen) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_entailment.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/entailment.py
from __future__ import annotations

_SCHEMA = {"type": "object", "properties": {"supported": {"type": "boolean"}},
           "required": ["supported"]}
_SYSTEM = ("You are a strict entailment judge. Decide whether the TEXT explicitly supports the CLAIM. "
           "Support means the text states or directly implies the claim. If the text is silent, "
           "partial, or only loosely related, it is NOT supported. Answer only via the schema.")

def _default_gen(system, prompt, schema):
    from bot.services.ollama_client import generate_json_sync
    return generate_json_sync(system, prompt, schema, model="granite4:tiny-h",
                              timeout=20.0, num_predict=16)

def entails(fact: str, text: str, *, gen=None) -> bool:
    gen = gen or _default_gen
    prompt = f"CLAIM:\n{fact}\n\nTEXT:\n{text}\n\nDoes the TEXT support the CLAIM?"
    out = gen(_SYSTEM, prompt, _SCHEMA)
    return bool(out and out.get("supported") is True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_entailment.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/entailment.py v2/tests/processing_debt/test_entailment.py
git commit -m "feat(processing-debt): shared Granite entailment judge"
```

---

## Task 4: `nuggetize.py` — atomic facts + vital/okay materiality

**Files:**
- Create: `eval/processing_debt/nuggetize.py`
- Test: `v2/tests/processing_debt/test_nuggetize.py`

**Interfaces:**
- Consumes: `OracleAnswer, Nugget`.
- Produces: `nuggetize(oracle: OracleAnswer, *, gen=None) -> list[Nugget]`. `gen` injectable as in Task 3.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_nuggetize.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import OracleAnswer
from eval.processing_debt.nuggetize import nuggetize

def test_nuggetize_maps_vital_and_text():
    gen = lambda system, prompt, schema: {"nuggets": [
        {"text": "MS CS requires a 4-year computing degree.", "vital": True},
        {"text": "The building has a nice lobby.", "vital": False}]}
    oa = OracleAnswer(question="admission?", answer="... long answer ...", citations=[])
    out = nuggetize(oa, gen=gen)
    assert len(out) == 2
    assert out[0].vital is True and out[1].vital is False
    assert out[0].text.startswith("MS CS requires")

def test_nuggetize_empty_on_failure():
    gen = lambda system, prompt, schema: None
    oa = OracleAnswer(question="q", answer="a", citations=[])
    assert nuggetize(oa, gen=gen) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_nuggetize.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/nuggetize.py
from __future__ import annotations
from eval.processing_debt.types import OracleAnswer, Nugget

_SCHEMA = {"type": "object", "properties": {"nuggets": {"type": "array", "items": {
    "type": "object",
    "properties": {"text": {"type": "string"}, "vital": {"type": "boolean"}},
    "required": ["text", "vital"]}}}, "required": ["nuggets"]}
_SYSTEM = ("Decompose the ANSWER to the QUESTION into atomic facts (each a single, self-contained, "
           "verifiable statement — no pronouns, no conjunctions). For each, set vital=true if it is "
           "essential to correctly answering the QUESTION, vital=false if it is helpful-but-incidental "
           "detail. Copy facts faithfully; do not add facts not present in the ANSWER.")

def _default_gen(system, prompt, schema):
    from bot.services.ollama_client import generate_json_sync
    return generate_json_sync(system, prompt, schema, model="granite4:tiny-h",
                              timeout=45.0, num_predict=768)

def nuggetize(oracle: OracleAnswer, *, gen=None) -> list[Nugget]:
    gen = gen or _default_gen
    prompt = f"QUESTION:\n{oracle.question}\n\nANSWER:\n{oracle.answer}"
    out = gen(_SYSTEM, prompt, _SCHEMA)
    if not out or not isinstance(out.get("nuggets"), list):
        return []
    res = []
    for n in out["nuggets"]:
        t = (n.get("text") or "").strip()
        if t:
            res.append(Nugget(text=t, vital=bool(n.get("vital"))))
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_nuggetize.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/nuggetize.py v2/tests/processing_debt/test_nuggetize.py
git commit -m "feat(processing-debt): oracle answer -> atomic facts with vital/okay materiality"
```

---

## Task 5: Fixture DB builder (shared test corpus)

**Files:**
- Create: `v2/tests/processing_debt/conftest.py`
- Test: `v2/tests/processing_debt/test_fixture_db.py`

**Interfaces:**
- Produces: pytest fixture `fixture_db(tmp_path) -> str` (path to a standalone SQLite file with a `nodes`, `edges`, `knowledge_items` schema and a handful of planted rows, one reachable by each probe).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_fixture_db.py
import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

def test_fixture_db_has_planted_rows(fixture_db):
    conn = sqlite3.connect(fixture_db)
    n_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_items = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
    assert n_nodes >= 1 and n_items >= 2
    # a publication-typed row exists (the excluded-type case)
    pub = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE type='publication'").fetchone()[0]
    assert pub >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_fixture_db.py -v`
Expected: FAIL with `fixture 'fixture_db' not found`

- [ ] **Step 3: Write minimal implementation**

```python
# v2/tests/processing_debt/conftest.py
import json, sqlite3
import pytest

@pytest.fixture
def fixture_db(tmp_path):
    db = tmp_path / "fixture.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE nodes (id INTEGER PRIMARY KEY, key TEXT, type TEXT, name TEXT, attrs TEXT);
      CREATE TABLE edges (id INTEGER PRIMARY KEY, src_id INT, type TEXT, dst_id INT,
                          category TEXT, attrs TEXT, is_active INT DEFAULT 1);
      CREATE TABLE knowledge_items (id INTEGER PRIMARY KEY, org_id INT, type TEXT, title TEXT,
                          content TEXT, metadata TEXT, is_active INT DEFAULT 1);
      CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, content, content='knowledge_items', content_rowid='id');
    """)
    # kg_probe target: a Person node with a role title in attrs/edges
    conn.execute("INSERT INTO nodes(id,key,type,name,attrs) VALUES (1,'people/pan-xu','Person','Pan Xu',?)",
                 (json.dumps({"office": "4310 GITC"}),))
    conn.execute("INSERT INTO nodes(id,key,type,name,attrs) VALUES (2,'org/cs','Org','Computer Science','{}')")
    conn.execute("INSERT INTO edges(id,src_id,type,dst_id,category,attrs) VALUES "
                 "(1,1,'has_role',2,'faculty',?)", (json.dumps({"titles": ["Assistant Professor"]}),))
    # fts_probe / grep_probe target in a NORMAL type
    conn.execute("INSERT INTO knowledge_items(id,type,title,content) VALUES "
                 "(10,'policy','MS CS Admission','MS in Computer Science requires a four-year computing degree.')")
    # excluded-type (publication) target — owned but normally excluded from answers → CONFIG stage
    conn.execute("INSERT INTO knowledge_items(id,type,title,content) VALUES "
                 "(11,'publication','Veil paper','Veil: A Storage and Communication Efficient Volume-Hiding Algorithm.')")
    conn.execute("INSERT INTO knowledge_fts(rowid,title,content) SELECT id,title,content FROM knowledge_items")
    conn.commit()
    conn.close()
    return str(db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_fixture_db.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add v2/tests/processing_debt/conftest.py v2/tests/processing_debt/test_fixture_db.py
git commit -m "test(processing-debt): hermetic fixture DB with one row per probe"
```

---

## Task 6: `presence_check.py` — the crux (4 union probes)

**Files:**
- Create: `eval/processing_debt/presence_check.py`
- Test: `v2/tests/processing_debt/test_presence_check.py`

**Interfaces:**
- Consumes: `PresenceResult, PresenceEvidence` from `types`; `entails` from `entailment` (injectable).
- Produces:
  - `kg_probe(conn, fact) -> list[PresenceEvidence]`
  - `fts_probe(conn, fact) -> list[PresenceEvidence]`
  - `grep_probe(conn, fact) -> list[PresenceEvidence]`
  - `embed_probe(conn, fact, embed_query, knn) -> list[PresenceEvidence]` (embed fn + knn fn injected)
  - `presence(conn, fact, *, embedder=None, entails_fn=None) -> PresenceResult`

Note: `embed_probe` is injected with `embed_query`/`knn` callables so unit tests skip Ollama+sqlite-vec; `presence()` wires the real `Embedder` and a sqlite-vec KNN by default, but the fixture tests exercise kg/fts/grep (which need no vectors) and assert the union.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_presence_check.py
import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.presence_check import kg_probe, fts_probe, grep_probe, presence

def _conn(fixture_db):
    c = sqlite3.connect(fixture_db); c.row_factory = sqlite3.Row; return c

def test_fts_probe_finds_normal_type(fixture_db):
    conn = _conn(fixture_db)
    ev = fts_probe(conn, "MS in Computer Science requires a four-year computing degree")
    assert any(e.probe == "fts_probe" and e.row_or_node_id == "10" for e in ev)

def test_fts_probe_finds_excluded_publication_type(fixture_db):
    conn = _conn(fixture_db)
    ev = fts_probe(conn, "Veil volume-hiding algorithm")
    assert any(e.item_type == "publication" for e in ev)

def test_kg_probe_finds_person(fixture_db):
    conn = _conn(fixture_db)
    ev = kg_probe(conn, "Pan Xu is an Assistant Professor")
    assert any(e.probe == "kg_probe" and e.row_or_node_id == "1" for e in ev)

def test_grep_probe_exact_string(fixture_db):
    conn = _conn(fixture_db)
    ev = grep_probe(conn, "four-year computing degree")
    assert any(e.probe == "grep_probe" for e in ev)

def test_presence_union_present_when_entails_true(fixture_db):
    conn = _conn(fixture_db)
    r = presence(conn, "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", entails_fn=lambda fact, text: True)
    assert r.present is True and "fts_probe" in r.probes_hit

def test_presence_absent_when_no_probe_and_no_entail(fixture_db):
    conn = _conn(fixture_db)
    r = presence(conn, "The provost announced a tuition freeze in 1998",
                 embedder="SKIP", entails_fn=lambda fact, text: False)
    assert r.present is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_presence_check.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/presence_check.py
from __future__ import annotations
import json, re, sqlite3
from eval.processing_debt.types import PresenceResult, PresenceEvidence

_STOP = {"the","a","an","is","are","of","in","to","and","for","by","with","on","at","as","who","what",
         "which","that","this","was","were","be","from","or","his","her","their","its","it"}

def _content_terms(fact: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']+", fact)
    return [t for t in toks if t.lower() not in _STOP and len(t) > 2]

def kg_probe(conn: sqlite3.Connection, fact: str) -> list[PresenceEvidence]:
    """Match capitalized multi-word spans (likely entity names) against node names/attrs."""
    names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", fact)
    out: list[PresenceEvidence] = []
    seen = set()
    for name in names:
        rows = conn.execute(
            "SELECT id, name, attrs FROM nodes WHERE name LIKE ? OR attrs LIKE ?",
            (f"%{name}%", f"%{name}%")).fetchall()
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(PresenceEvidence("node", str(r["id"]), r["name"] or "", "kg_probe"))
    return out

def fts_probe(conn: sqlite3.Connection, fact: str) -> list[PresenceEvidence]:
    terms = _content_terms(fact)
    if not terms:
        return []
    query = " OR ".join(f'"{t}"' for t in terms[:12])
    out: list[PresenceEvidence] = []
    try:
        rows = conn.execute(
            "SELECT ki.id AS id, ki.type AS type, ki.content AS content "
            "FROM knowledge_fts f JOIN knowledge_items ki ON ki.id = f.rowid "
            "WHERE knowledge_fts MATCH ? LIMIT 20", (query,)).fetchall()
    except sqlite3.OperationalError:
        return []
    for r in rows:
        out.append(PresenceEvidence("knowledge_item", str(r["id"]), (r["content"] or "")[:300],
                                    "fts_probe", item_type=r["type"]))
    return out

def grep_probe(conn: sqlite3.Connection, fact: str) -> list[PresenceEvidence]:
    """Substring hunt for the longest content phrases (>=3 words) over raw text."""
    phrases = re.findall(r"[A-Za-z0-9][A-Za-z0-9\- ]{12,}", fact)
    out: list[PresenceEvidence] = []
    for ph in phrases:
        ph = ph.strip()
        rows = conn.execute(
            "SELECT id, type, content FROM knowledge_items WHERE content LIKE ? LIMIT 10",
            (f"%{ph}%",)).fetchall()
        for r in rows:
            out.append(PresenceEvidence("knowledge_item", str(r["id"]), ph, "grep_probe",
                                        item_type=r["type"]))
    return out

def embed_probe(conn, fact, embed_query, knn) -> list[PresenceEvidence]:
    vec = embed_query(fact)
    if vec is None:
        return []
    hits = knn(conn, vec, k=100)   # -> list[(item_id, type, content)]
    return [PresenceEvidence("knowledge_item", str(i), (c or "")[:300], "embed_probe", item_type=t)
            for (i, t, c) in hits]

def _real_embed_and_knn():
    from v2.core.retrieval.embedder import Embedder
    emb = Embedder()
    def embed_query(text): return emb.embed_query(text)
    def knn(conn, vec, k=100):
        blob = json.dumps(vec)
        try:
            rows = conn.execute(
                "SELECT ki.id, ki.type, ki.content FROM knowledge_vectors kv "
                "JOIN knowledge_items ki ON ki.id = kv.item_id "
                "WHERE kv.embedding MATCH ? AND k = ? ORDER BY distance", (blob, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r[0], r[1], r[2]) for r in rows]
    return embed_query, knn

def presence(conn, fact, *, embedder=None, entails_fn=None) -> PresenceResult:
    from eval.processing_debt.entailment import entails as _entails
    entails_fn = entails_fn or _entails
    evidence: list[PresenceEvidence] = []
    evidence += kg_probe(conn, fact)
    evidence += fts_probe(conn, fact)
    evidence += grep_probe(conn, fact)
    if embedder != "SKIP":
        embed_query, knn = _real_embed_and_knn()
        evidence += embed_probe(conn, fact, embed_query, knn)
    # confirm: present iff entailment holds for at least one candidate span
    confirmed: list[PresenceEvidence] = []
    for ev in evidence:
        if entails_fn(fact, ev.span):
            confirmed.append(ev)
    probes_hit = sorted({e.probe for e in confirmed})
    return PresenceResult(present=bool(confirmed), probes_hit=probes_hit, evidence=confirmed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_presence_check.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/presence_check.py v2/tests/processing_debt/test_presence_check.py
git commit -m "feat(processing-debt): exhaustive non-production presence check (kg/fts/embed/grep union)"
```

---

## Task 7: `xray.py` — structured live-pipeline X-ray

**Files:**
- Create: `eval/processing_debt/xray.py`
- Test: `v2/tests/processing_debt/test_xray.py`

**Interfaces:**
- Consumes: `XRay` from `types`.
- Produces: `xray(conn, question, *, embedder=None, reranker=None) -> XRay`. Reuses the exact calls `scripts/trace_query.py` uses: `route(conn, q)`, `V2Retriever(conn, emb).retrieve(q, limit=50, group_by_entity=False)` for the fused pool, a reranked retrieve for CE + top5.

- [ ] **Step 1: Write the failing test** (uses monkeypatched retriever/route so it runs offline)

```python
# v2/tests/processing_debt/test_xray.py
import sys, types as _t
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import eval.processing_debt.xray as X
from eval.processing_debt.types import XRay

class _Chunk:
    def __init__(self, item_id, ce=None): self.item_id=item_id; self.ce_score=ce; self.content=f"c{item_id}"

def test_xray_assembles_pool_and_top5(monkeypatch):
    monkeypatch.setattr(X, "_route", lambda conn, q: _t.SimpleNamespace(family="rag", skill=None))
    fused = [_Chunk(10), _Chunk(11), _Chunk(12)]
    reranked = [_Chunk(11, 0.9), _Chunk(10, 0.4), _Chunk(12, 0.1)]
    monkeypatch.setattr(X, "_fused_pool", lambda conn, q, emb: fused)
    monkeypatch.setattr(X, "_reranked", lambda conn, q, emb, rer: reranked)
    xr = X.xray("conn", "who is x", embedder="E", reranker="R")
    assert xr.fused_pool_ids == [10, 11, 12]
    assert xr.top5_ids == [11, 10, 12]
    assert xr.ce_scores[11] == 0.9
    assert xr.router_family == "rag"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_xray.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/xray.py
from __future__ import annotations
from eval.processing_debt.types import XRay

def _route(conn, q):
    from v2.core.retrieval.router import route
    return route(conn, q)

def _fused_pool(conn, q, emb):
    from v2.core.retrieval.retriever import V2Retriever
    return V2Retriever(conn, emb).retrieve(q, limit=50, group_by_entity=False)

def _reranked(conn, q, emb, rer):
    from v2.core.retrieval.retriever import V2Retriever
    return V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=50, group_by_entity=False)

def xray(conn, question, *, embedder=None, reranker=None) -> XRay:
    if embedder is None:
        from v2.core.retrieval.embedder import Embedder
        embedder = Embedder()
    if reranker is None:
        from v2.core.retrieval.reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker()
    r = _route(conn, question)
    fused = _fused_pool(conn, question, embedder)
    reranked = _reranked(conn, question, embedder, reranker)
    ce = {c.item_id: c.ce_score for c in reranked if getattr(c, "ce_score", None) is not None}
    top5 = [c.item_id for c in reranked[:5]]
    return XRay(question=question,
                router_family=getattr(r, "family", None),
                router_skill=getattr(r, "skill", None),
                fused_pool_ids=[c.item_id for c in fused],
                top5_ids=top5, ce_scores=ce,
                tier_primary_miss=(len(reranked) == 0),
                answer=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_xray.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/xray.py v2/tests/processing_debt/test_xray.py
git commit -m "feat(processing-debt): structured live-pipeline x-ray (route + fused + reranked)"
```

> **Note:** the production answer string (for IN_ANSWER) is produced separately by the real pipeline in `classify.py`/`run_pilot.py` via `scripts/ask.sh --answer` (captured once per question and cached); `XRay.answer` is filled there. Router `family`/`skill` attribute names are confirmed against `v2/core/retrieval/router.py:Route` during Task 12 integration; if they differ, adjust the two `getattr` lines only.

---

## Task 8: `oracle_guard.py` — citation-support + authority flag

**Files:**
- Create: `eval/processing_debt/oracle_guard.py`
- Test: `v2/tests/processing_debt/test_oracle_guard.py`

**Interfaces:**
- Consumes: `OracleAnswer, Nugget, GuardVerdict`; `entails`.
- Produces: `guard(nugget, oracle, *, fetch=None, entails_fn=None, is_internal=None) -> GuardVerdict`. `fetch(url)->str` and `is_internal(fact)->bool` injectable.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_oracle_guard.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import OracleAnswer, OracleCitation, Nugget
from eval.processing_debt.oracle_guard import guard

def _oracle():
    return OracleAnswer("q", "ans", [OracleCitation("https://cs.njit.edu/x")])

def test_guard_supported_when_page_entails():
    v = guard(Nugget("Pan Xu is Assistant Professor", True), _oracle(),
              fetch=lambda u: "Pan Xu, Assistant Professor of CS", entails_fn=lambda f, t: True,
              is_internal=lambda f: False)
    assert v.verdict == "supported"

def test_guard_unsupported_when_page_silent():
    v = guard(Nugget("Pan Xu won a Nobel Prize", True), _oracle(),
              fetch=lambda u: "Pan Xu, Assistant Professor of CS", entails_fn=lambda f, t: False,
              is_internal=lambda f: False)
    assert v.verdict == "unsupported"

def test_guard_authority_flag_on_internal():
    v = guard(Nugget("The GSA president is Alice", True), _oracle(),
              fetch=lambda u: "irrelevant", entails_fn=lambda f, t: False,
              is_internal=lambda f: True)
    assert v.verdict == "we_are_authority"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_oracle_guard.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/oracle_guard.py
from __future__ import annotations
import urllib.request, re
from eval.processing_debt.types import OracleAnswer, Nugget, GuardVerdict

UA = "GSA-Gateway-Research/1.0"
_INTERNAL_HINTS = ("gsa", "graduate student association", "officer", "president of the gsa", "rgo", "club")

def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    return re.sub(r"<[^>]+>", " ", html)   # crude tag strip; sufficient for entailment span

def _default_is_internal(fact: str) -> bool:
    f = fact.lower()
    return any(h in f for h in _INTERNAL_HINTS)

def guard(nugget: Nugget, oracle: OracleAnswer, *, fetch=None, entails_fn=None, is_internal=None) -> GuardVerdict:
    from eval.processing_debt.entailment import entails as _entails
    fetch = fetch or _default_fetch
    entails_fn = entails_fn or _entails
    is_internal = is_internal or _default_is_internal
    if is_internal(nugget.text):
        return GuardVerdict("we_are_authority")
    for cite in oracle.citations:
        try:
            page = fetch(cite.url)
        except Exception:
            continue
        if page and entails_fn(nugget.text, page[:8000]):
            return GuardVerdict("supported", cited_url=cite.url, evidence_span=page[:300])
    return GuardVerdict("unsupported")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_oracle_guard.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/oracle_guard.py v2/tests/processing_debt/test_oracle_guard.py
git commit -m "feat(processing-debt): oracle-guard (citation-support NLI + we-are-authority flag)"
```

---

## Task 9: `erag_attrib.py` + `attribute.py` — stage attribution

**Files:**
- Create: `eval/processing_debt/erag_attrib.py`, `eval/processing_debt/attribute.py`
- Test: `v2/tests/processing_debt/test_attribute.py`

**Interfaces:**
- Consumes: `PresenceResult, XRay, Attribution`; `entails`.
- Produces:
  - `chunk_yields_fact(conn, item_id, question, fact, *, compose=None, entails_fn=None) -> bool` (eRAG: does this one chunk alone let the pipeline state the fact).
  - `attribute(conn, fact, presence, xray, *, erag=None) -> Attribution` implementing §3.3 of the design.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_attribute.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import PresenceResult, PresenceEvidence, XRay
from eval.processing_debt.attribute import attribute

def _xray(pool, top5, family="rag", skill=None):
    return XRay("q", family, skill, pool, top5, {i: 0.5 for i in top5}, False, "answer text")

def _pres(item_id, probe="fts_probe", item_type="policy"):
    return PresenceResult(True, [probe],
        [PresenceEvidence("knowledge_item", str(item_id), "span", probe, item_type=item_type)])

def test_config_when_only_excluded_publication(fixture_db=None):
    a = attribute(None, "fact", _pres(11, "fts_probe", "publication"), _xray([10], [10]))
    assert a.stage == "CONFIG"

def test_pool_when_evidence_chunk_absent_from_pool():
    a = attribute(None, "fact", _pres(99), _xray([10, 11], [10, 11]))
    assert a.stage == "POOL"

def test_rank_when_in_pool_below_top5_and_chunk_yields():
    a = attribute(None, "fact", _pres(12), _xray([10, 11, 12], [10, 11]),
                  erag=lambda conn, iid, q, f: True)
    assert a.stage == "RANK"

def test_compose_when_in_top5_but_missing_from_answer():
    a = attribute(None, "fact", _pres(10), _xray([10, 11], [10, 11]))
    assert a.stage == "COMPOSE"

def test_router_when_kg_probe_and_router_not_structured():
    pres = PresenceResult(True, ["kg_probe"],
        [PresenceEvidence("node", "1", "Pan Xu", "kg_probe")])
    a = attribute(None, "fact", pres, _xray([], [], family="rag", skill=None))
    assert a.stage == "ROUTER"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_attribute.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/erag_attrib.py
from __future__ import annotations

def _default_compose(conn, item_id, question):
    """Compose an answer from ONLY this chunk, using the real compose path. Returns text."""
    import asyncio
    row = conn.execute("SELECT title, content FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return ""
    facts = f"{row[0] or ''}: {row[1] or ''}"
    from bot.services.ollama_client import OllamaClient
    async def _run():
        return await OllamaClient().compose_from_rows(question, facts)
    try:
        return asyncio.run(_run()) or ""
    except Exception:
        return ""

def chunk_yields_fact(conn, item_id, question, fact, *, compose=None, entails_fn=None) -> bool:
    from eval.processing_debt.entailment import entails as _entails
    compose = compose or _default_compose
    entails_fn = entails_fn or _entails
    text = compose(conn, item_id, question)
    return bool(text and entails_fn(fact, text))
```
```python
# eval/processing_debt/attribute.py
from __future__ import annotations
from eval.processing_debt.types import Attribution, PresenceResult, XRay

_EXCLUDED_TYPES = {"publication"}  # mirrors production retriever.exclude_types default

def attribute(conn, fact: str, presence: PresenceResult, xray: XRay, *, erag=None) -> Attribution:
    from eval.processing_debt.erag_attrib import chunk_yields_fact
    erag = erag or chunk_yields_fact
    ev = presence.evidence

    # CONFIG: only found in an excluded knowledge_items type (owned, but deliberately not served)
    ki = [e for e in ev if e.source_type == "knowledge_item"]
    if ki and all((e.item_type in _EXCLUDED_TYPES) for e in ki) and not any(e.source_type == "node" for e in ev):
        return Attribution("CONFIG", "fact lives only in an excluded item type")

    # ROUTER: a structured/KG fact whose owner skill was not routed
    if any(e.source_type == "node" for e in ev) and (xray.router_skill is None):
        return Attribution("ROUTER", "kg-owned fact but router did not hit a structured skill")

    # locate a servable (non-excluded) knowledge_item chunk id
    servable = [int(e.row_or_node_id) for e in ki if e.item_type not in _EXCLUDED_TYPES]
    if servable:
        cid = servable[0]
        if cid not in xray.fused_pool_ids:
            return Attribution("POOL", "evidence chunk absent from the fused candidate pool")
        if cid not in xray.top5_ids:
            if erag(conn, cid, xray.question, fact):
                return Attribution("RANK", "chunk in pool, below top-5, but alone yields the fact")
            return Attribution("POOL", "chunk in pool but not utile for the fact")
        return Attribution("COMPOSE", "chunk in top-5 context but fact absent from the answer")

    return Attribution("UNRESOLVED", "no servable evidence chunk mapped to a stage")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_attribute.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/erag_attrib.py eval/processing_debt/attribute.py v2/tests/processing_debt/test_attribute.py
git commit -m "feat(processing-debt): eRAG chunk-utility + stage attribution decision tree"
```

---

## Task 10: `classify.py` — orchestrate one fact end-to-end

**Files:**
- Create: `eval/processing_debt/classify.py`
- Test: `v2/tests/processing_debt/test_classify.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `classify_fact(conn, nugget, oracle, our_answer, xray, *, deps=None) -> FactRecord`. `deps` is a small struct of injected callables (`guard`, `entails`, `presence`, `attribute`) so the orchestration is unit-tested without Ollama/DB.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_classify.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import (OracleAnswer, Nugget, GuardVerdict, PresenceResult,
                                        PresenceEvidence, XRay, Attribution)
from eval.processing_debt.classify import classify_fact, Deps

def _xr(): return XRay("q", "rag", None, [10], [10], {10: 0.5}, False, "our answer")

def _deps(**over):
    base = dict(
        guard=lambda n, o: GuardVerdict("supported"),
        entails=lambda fact, text: False,          # not in our answer
        presence=lambda conn, fact: PresenceResult(True, ["fts_probe"],
            [PresenceEvidence("knowledge_item", "10", "s", "fts_probe", item_type="policy")]),
        attribute=lambda conn, fact, pres, xray: Attribution("COMPOSE", "r"))
    base.update(over); return Deps(**base)

def test_dropped_when_guard_unsupported():
    d = _deps(guard=lambda n, o: GuardVerdict("unsupported"))
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "DROPPED_ORACLE"

def test_in_answer_when_entailed():
    d = _deps(entails=lambda fact, text: True)
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "IN_ANSWER" and fr.stage is None

def test_owned_not_surfaced_with_stage():
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=_deps())
    assert fr.fact_class == "OWNED_NOT_SURFACED" and fr.stage == "COMPOSE"

def test_not_owned_when_absent():
    d = _deps(presence=lambda conn, fact: PresenceResult(False, [], []))
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "NOT_OWNED" and fr.stage is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/classify.py
from __future__ import annotations
from dataclasses import dataclass
from eval.processing_debt.types import OracleAnswer, Nugget, XRay, FactRecord

@dataclass
class Deps:
    guard: object
    entails: object
    presence: object
    attribute: object

def _default_deps() -> Deps:
    from eval.processing_debt.oracle_guard import guard
    from eval.processing_debt.entailment import entails
    from eval.processing_debt.presence_check import presence
    from eval.processing_debt.attribute import attribute
    return Deps(guard=guard, entails=entails, presence=presence, attribute=attribute)

def classify_fact(conn, nugget: Nugget, oracle: OracleAnswer, our_answer: str, xray: XRay,
                  *, stratum: str = "", deps: Deps | None = None) -> FactRecord:
    deps = deps or _default_deps()
    gv = deps.guard(nugget, oracle)
    if gv.verdict != "supported":
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=False, presence=_empty(), fact_class="DROPPED_ORACLE",
                          stage=None, xray_ref=oracle.question)
    in_ans = deps.entails(nugget.text, our_answer or "")
    if in_ans:
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=True, presence=_empty(), fact_class="IN_ANSWER",
                          stage=None, xray_ref=oracle.question)
    pres = deps.presence(conn, nugget.text)
    if not pres.present:
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=False, presence=pres, fact_class="NOT_OWNED",
                          stage=None, xray_ref=oracle.question)
    attr = deps.attribute(conn, nugget.text, pres, xray)
    return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                      in_answer=False, presence=pres, fact_class="OWNED_NOT_SURFACED",
                      stage=attr.stage, xray_ref=oracle.question)

def _empty():
    from eval.processing_debt.types import PresenceResult
    return PresenceResult(False, [], [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_classify.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/classify.py v2/tests/processing_debt/test_classify.py
git commit -m "feat(processing-debt): per-fact classification orchestrator"
```

---

## Task 11: `adjudicate.py` — human-label CSV + Cohen's κ

**Files:**
- Create: `eval/processing_debt/adjudicate.py`
- Test: `v2/tests/processing_debt/test_adjudicate.py`

**Interfaces:**
- Produces: `emit_csv(records, path)`; `cohen_kappa(machine: list[bool], human: list[bool]) -> float`; `ingest_labels(path) -> dict[str, bool]`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_adjudicate.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.adjudicate import cohen_kappa

def test_kappa_perfect_agreement():
    assert round(cohen_kappa([True, False, True, False], [True, False, True, False]), 3) == 1.0

def test_kappa_chance_agreement_near_zero():
    m = [True, True, False, False]
    h = [True, False, True, False]
    assert abs(cohen_kappa(m, h)) < 0.34

def test_kappa_handles_all_same_class():
    # degenerate: both all-True -> define kappa = 1.0 (perfect, no disagreement)
    assert cohen_kappa([True, True], [True, True]) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_adjudicate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/adjudicate.py
from __future__ import annotations
import csv

def cohen_kappa(machine: list[bool], human: list[bool]) -> float:
    n = len(machine)
    assert n == len(human) and n > 0
    po = sum(1 for m, h in zip(machine, human) if m == h) / n
    pm_t = sum(machine) / n; ph_t = sum(human) / n
    pe = pm_t * ph_t + (1 - pm_t) * (1 - ph_t)
    if pe == 1.0:                      # no variance (all same) → treat identical labels as perfect
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)

def emit_csv(records, path: str) -> None:
    """records: list[FactRecord]. Emit the columns a human adjudicates."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "question", "fact_text", "vital", "machine_in_answer",
                    "machine_presence", "machine_class", "machine_stage",
                    "human_in_answer", "human_presence", "human_stage_ok"])
        for i, r in enumerate(records):
            w.writerow([i, r.question, r.fact_text, int(r.vital), int(r.in_answer),
                        int(r.presence.present), r.fact_class, r.stage or "", "", "", ""])

def ingest_labels(path: str) -> dict[str, list[bool]]:
    """Read back human_* columns. Returns dict of decision -> list[bool] aligned to machine lists."""
    hi, hp = [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("human_in_answer", "").strip() != "":
                hi.append(row["human_in_answer"].strip() in ("1", "true", "True"))
            if row.get("human_presence", "").strip() != "":
                hp.append(row["human_presence"].strip() in ("1", "true", "True"))
    return {"in_answer": hi, "presence": hp}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_adjudicate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/adjudicate.py v2/tests/processing_debt/test_adjudicate.py
git commit -m "feat(processing-debt): human-label CSV emit/ingest + Cohen's kappa"
```

---

## Task 12: `sample.py` + `run_pilot.py` — stratified sampler & driver (integration)

**Files:**
- Create: `eval/processing_debt/sample.py`, `eval/processing_debt/run_pilot.py`
- Test: `v2/tests/processing_debt/test_sample.py`

**Interfaces:**
- Produces:
  - `sample(seed=0, path_label=None) -> list[tuple[str, str]]` — (question, stratum) pairs per §5 quotas, drawn from `docs/SampleQuestions/`. `path_label(question)->str` injectable (default runs the live pipeline once to read the tier).
  - `run_pilot(sample_pairs, *, conn=None, out="eval/processing_debt/out/facts.jsonl") -> None` — the real driver: for each question, capture our production answer via `scripts/ask.sh --answer` (cached), oracle answer, nuggetize, guard, classify each vital nugget, write JSONL.

- [ ] **Step 1: Write the failing test** (sampler quotas only; driver is exercised live in Task 14)

```python
# v2/tests/processing_debt/test_sample.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.sample import STRATA, allocate

def test_strata_sum_to_50():
    assert sum(n for _, n in STRATA) == 50

def test_allocate_respects_quota_and_dedup():
    pools = {name: [f"{name} q{i}" for i in range(50)] for name, _ in STRATA}
    picked = allocate(pools, seed=0)
    from collections import Counter
    c = Counter(stratum for _, stratum in picked)
    for name, n in STRATA:
        assert c[name] == n
    assert len(set(q for q, _ in picked)) == len(picked)   # no dup questions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_sample.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/sample.py
from __future__ import annotations
import random
from pathlib import Path

# (stratum name, N) — must sum to 50 (design §5)
STRATA = [
    ("db_router_hit", 10), ("db_rag", 10), ("db_live_fallback", 6),
    ("db_abstain", 8), ("positive_control", 5), ("oracle_blind", 3), ("web_needing", 8),
]

def allocate(pools: dict[str, list[str]], seed: int = 0) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    picked, used = [], set()
    for name, n in STRATA:
        cand = [q for q in pools.get(name, []) if q not in used]
        rng.shuffle(cand)
        chosen = cand[:n]
        for q in chosen:
            used.add(q); picked.append((q, name))
    return picked

def _load_questions() -> tuple[list[str], list[str]]:
    base = Path(__file__).resolve().parents[2] / "docs" / "SampleQuestions"
    db = [l.strip() for l in (base / "Question based on DB.txt").read_text().splitlines() if l.strip()]
    web = [l.strip() for l in (base / "Questions based on internet.txt").read_text().splitlines() if l.strip()]
    return db, web

def sample(seed: int = 0, path_label=None) -> list[tuple[str, str]]:
    """Build the 50 by bucketing DB questions by live pipeline path; web questions fill web_needing.
    path_label(q)->one of {router_hit,rag,live_fallback,abstain}."""
    from eval.processing_debt.pathlabel import label_path   # thin wrapper over xray/tier
    path_label = path_label or label_path
    db, web = _load_questions()
    pools = {name: [] for name, _ in STRATA}
    pools["web_needing"] = web
    pools["positive_control"] = []   # curated by hand in out/controls_positive.txt (see note)
    pools["oracle_blind"] = []       # curated by hand in out/controls_internal.txt
    for q in db:
        p = path_label(q)
        key = {"router_hit": "db_router_hit", "rag": "db_rag",
               "live_fallback": "db_live_fallback", "abstain": "db_abstain"}.get(p)
        if key:
            pools[key].append(q)
    # controls loaded from curated files if present
    for name, fn in [("positive_control", "controls_positive.txt"), ("oracle_blind", "controls_internal.txt")]:
        f = Path("eval/processing_debt/out") / fn
        if f.exists():
            pools[name] = [l.strip() for l in f.read_text().splitlines() if l.strip()]
    return allocate(pools, seed=seed)
```
```python
# eval/processing_debt/run_pilot.py
from __future__ import annotations
import json, subprocess
from pathlib import Path
from eval.processing_debt.dbconn import get_ro_connection
from eval.processing_debt.oracle_brave import ask_oracle
from eval.processing_debt.nuggetize import nuggetize
from eval.processing_debt.xray import xray
from eval.processing_debt.classify import classify_fact

def _our_answer(question: str, cache: dict) -> str:
    if question in cache:
        return cache[question]
    try:
        out = subprocess.run(["bash", "scripts/ask.sh", question, "--answer"],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:
        out = ""
    # the real answer is printed after the "5. FINAL LLM ANSWER" header
    ans = out.split("FINAL LLM ANSWER", 1)[-1].strip() if "FINAL LLM ANSWER" in out else ""
    cache[question] = ans
    return ans

def run_pilot(sample_pairs, *, conn=None, out="eval/processing_debt/out/facts.jsonl") -> None:
    conn = conn or get_ro_connection()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    ans_cache: dict[str, str] = {}
    with open(out, "w") as f:
        for question, stratum in sample_pairs:
            oracle = ask_oracle(question)
            our = _our_answer(question, ans_cache)
            xr = xray(conn, question); xr.answer = our
            for nug in nuggetize(oracle):
                if not nug.vital:      # record okay-facts too, but they don't count in the metric
                    pass
                fr = classify_fact(conn, nug, oracle, our, xr, stratum=stratum)
                f.write(json.dumps(fr.as_dict()) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_sample.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/sample.py eval/processing_debt/run_pilot.py v2/tests/processing_debt/test_sample.py
git commit -m "feat(processing-debt): stratified sampler + live pilot driver"
```

> **Note:** `pathlabel.py` (a ~15-line wrapper mapping a question → {router_hit,rag,live_fallback,abstain} by reading `xray`/tier) and the two curated control files are produced during Task 14 setup; the sampler degrades gracefully (empty control pools) until then. The `ask.sh` answer-delimiter (`FINAL LLM ANSWER`) is confirmed against real output in Task 14; adjust the split token if the header text differs.

---

## Task 13: `report.py` — aggregate + SC1–SC6 gate

**Files:**
- Create: `eval/processing_debt/report.py`
- Test: `v2/tests/processing_debt/test_report.py`

**Interfaces:**
- Produces: `build_report(records, kappas) -> dict` (headline debt %, per-stage table, per-stratum, SC1–SC6 pass/fail) and `render_md(report) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/processing_debt/test_report.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import PresenceResult
from eval.processing_debt.report import build_report

def _rec(cls, stage=None, vital=True, stratum="db_rag"):
    from eval.processing_debt.types import FactRecord
    return FactRecord("q", stratum, "f", vital, "supported", cls == "IN_ANSWER",
                      PresenceResult(cls != "NOT_OWNED", [], []), cls, stage, "q")

def test_debt_ratio_and_stage_table():
    recs = [_rec("IN_ANSWER"), _rec("OWNED_NOT_SURFACED", "POOL"),
            _rec("OWNED_NOT_SURFACED", "COMPOSE"), _rec("NOT_OWNED")]
    rep = build_report(recs, {"in_answer": 0.7, "presence": 0.65})
    # denominator = IN_ANSWER + OWNED_NOT_SURFACED (vital) = 3; owned-miss = 2
    assert abs(rep["processing_debt"] - (2/3)) < 1e-6
    assert rep["stage_counts"]["POOL"] == 1 and rep["stage_counts"]["COMPOSE"] == 1

def test_sc1_gate_pass_when_kappa_high():
    rep = build_report([_rec("OWNED_NOT_SURFACED", "POOL")] * 5, {"in_answer": 0.7, "presence": 0.7})
    assert rep["SC1"] is True

def test_sc1_gate_fail_when_kappa_low():
    rep = build_report([_rec("OWNED_NOT_SURFACED", "POOL")], {"in_answer": 0.3, "presence": 0.7})
    assert rep["SC1"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/processing_debt/report.py
from __future__ import annotations
from collections import Counter

_STAGES = ["ROUTER", "POOL", "RANK", "COMPOSE", "CONFIG", "UNRESOLVED"]

def build_report(records, kappas: dict) -> dict:
    vital = [r for r in records if r.vital]
    in_ans = [r for r in vital if r.fact_class == "IN_ANSWER"]
    owned_miss = [r for r in vital if r.fact_class == "OWNED_NOT_SURFACED"]
    denom = len(in_ans) + len(owned_miss)
    debt = (len(owned_miss) / denom) if denom else 0.0
    stage_counts = Counter(r.stage for r in owned_miss)
    strat_counts = Counter(r.stratum for r in owned_miss)
    unresolved = stage_counts.get("UNRESOLVED", 0)
    attributed = len(owned_miss) - unresolved
    sc1 = all(k >= 0.6 for k in kappas.values()) if kappas else False
    sc5 = (attributed / len(owned_miss) >= 0.70) if owned_miss else False
    return {
        "processing_debt": debt,
        "n_vital": len(vital), "n_in_answer": len(in_ans), "n_owned_miss": len(owned_miss),
        "stage_counts": {s: stage_counts.get(s, 0) for s in _STAGES},
        "stratum_counts": dict(strat_counts),
        "kappas": kappas,
        "SC1": sc1,                              # judge trust gate
        "SC4": len(owned_miss) >= 5,             # yield
        "SC5": sc5,                              # attribution unambiguous >=70%
    }

def render_md(report: dict) -> str:
    lines = ["# Processing-Debt Pilot Report", "",
             f"**Processing Debt (demand-weighted): {report['processing_debt']*100:.1f}%** "
             f"({report['n_owned_miss']} owned-misses / {report['n_in_answer']+report['n_owned_miss']} vital owned facts)",
             "", "## Per-stage", "", "| Stage | Count |", "|---|---|"]
    for s, c in report["stage_counts"].items():
        lines.append(f"| {s} | {c} |")
    lines += ["", "## Instrument validity (Cohen's κ)", ""]
    for k, v in report["kappas"].items():
        lines.append(f"- {k}: κ={v:.3f}")
    lines += ["", "## Success criteria",
              f"- SC1 (κ≥0.6 both decisions): {'PASS' if report['SC1'] else 'FAIL'}",
              f"- SC4 (≥5 owned-misses): {'PASS' if report['SC4'] else 'FAIL'}",
              f"- SC5 (≥70% attributed): {'PASS' if report['SC5'] else 'FAIL'}"]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/test_report.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add eval/processing_debt/report.py v2/tests/processing_debt/test_report.py
git commit -m "feat(processing-debt): aggregate report + SC1/SC4/SC5 gates"
```

---

## Task 14: Live integration dry-run (controls first, then the 50) + interface reconciliation

**Files:**
- Create: `eval/processing_debt/pathlabel.py`, `eval/processing_debt/out/controls_positive.txt`, `eval/processing_debt/out/controls_internal.txt`
- Modify (only if reconciliation needs it): `oracle_brave.py:_parse`, `xray.py` getattr lines, `run_pilot.py` answer delimiter

**Interfaces:**
- Produces: `label_path(question) -> str`.

- [ ] **Step 1: Confirm live interfaces with a one-shot probe (no real oracle spend beyond 1)**

Run:
```bash
.venv/bin/python -c "
from v2.core.retrieval.router import route, Route
import inspect; print('Route fields:', [f for f in dir(Route) if not f.startswith('_')])
"
bash scripts/ask.sh 'who is Pan Xu' --answer | tail -20   # confirm the FINAL-answer delimiter text
.venv/bin/python -c "
from eval.processing_debt.oracle_brave import ask_oracle
oa=ask_oracle('what are the admission requirements for NJIT MS Computer Science')
print('CIT:', oa.citations[:2]); print('ANS:', oa.answer[:200])
"
```
Expected: prints Route's real attribute names, the exact final-answer header, and a live oracle answer **with citations** (this is the 1 permitted live oracle call; it is cached). If any differ from the plan's assumptions, fix the three noted spots and re-run their unit tests.

- [ ] **Step 2: Write the control files (curated by owner + Claude)**

`eval/processing_debt/out/controls_positive.txt` — 5 questions we KNOW we answer completely (e.g. `who is the chair of computer science`, `who are the GSA officers`, `what is Pan Xu's h-index`, …). `eval/processing_debt/out/controls_internal.txt` — 3 GSA-internal questions the oracle can't know (e.g. `who is the GSA VP of Academic Affairs`, …).

- [ ] **Step 3: Write `pathlabel.py`**

```python
# eval/processing_debt/pathlabel.py
from __future__ import annotations
from eval.processing_debt.dbconn import get_ro_connection
from eval.processing_debt.xray import xray

_CONN = None
def _conn():
    global _CONN
    if _CONN is None: _CONN = get_ro_connection()
    return _CONN

def label_path(question: str) -> str:
    xr = xray(_conn(), question)
    if xr.router_skill:            return "router_hit"
    if xr.tier_primary_miss:       return "live_fallback"   # KB miss → prod would live-fallback/abstain
    return "rag"
```
(For the pilot we treat the abstain vs live split coarsely; the driver records the real tier per question anyway. `db_abstain` questions are hand-topped-up from known abstain cases if the automatic bucket underfills.)

- [ ] **Step 4: Run the CONTROLS ONLY through the driver first (integration gate)**

Run:
```bash
.venv/bin/python -c "
from eval.processing_debt.run_pilot import run_pilot
from eval.processing_debt.sample import _load_questions
pairs=[(q,'positive_control') for q in open('eval/processing_debt/out/controls_positive.txt').read().split(chr(10)) if q.strip()]
pairs+=[(q,'oracle_blind') for q in open('eval/processing_debt/out/controls_internal.txt').read().split(chr(10)) if q.strip()]
run_pilot(pairs, out='eval/processing_debt/out/controls.jsonl')
"
.venv/bin/python -c "
import json
recs=[json.loads(l) for l in open('eval/processing_debt/out/controls.jsonl')]
miss=[r for r in recs if r['vital'] and r['fact_class']=='OWNED_NOT_SURFACED' and r['stratum']=='positive_control']
print('positive-control owned-misses (expect <=1):', len(miss))
blind=[r for r in recs if r['stratum']=='oracle_blind']
flagged=[r for r in blind if r['fact_class']=='DROPPED_ORACLE']
print('oracle-blind facts:', len(blind), 'guard-flagged:', len(flagged))
"
```
Expected: **SC2** — positive-control owned-misses ≤ 1 (else the decomposition/materiality/presence chain is broken → STOP, fix, do not spend on the full 50). **SC3** — oracle-blind facts are guard-flagged/authority. This is the go/no-go before spending on the full sample.

- [ ] **Step 5: Commit the reconciliation + controls**

```bash
git add eval/processing_debt/pathlabel.py eval/processing_debt/out/controls_positive.txt eval/processing_debt/out/controls_internal.txt eval/processing_debt/oracle_brave.py eval/processing_debt/xray.py eval/processing_debt/run_pilot.py
git commit -m "feat(processing-debt): live interface reconciliation + control-set integration gate"
```

---

## Task 15: Full pilot run + adjudication + report (the deliverable)

**Files:**
- Create: `eval/processing_debt/out/facts.jsonl`, `eval/processing_debt/out/adjudicate.csv`, `eval/processing_debt/out/pilot_report.md` (generated artifacts — commit the report, gitignore the cache)
- Create: `eval/processing_debt/.gitignore` (ignore `.cache/`)

- [ ] **Step 1: Build the sample and run the full 50 (≤ $2.85, cached)**

Run:
```bash
.venv/bin/python -c "
from eval.processing_debt.sample import sample
from eval.processing_debt.run_pilot import run_pilot
pairs = sample(seed=0)
print('sampled', len(pairs), 'questions')
run_pilot(pairs)
"
```
Expected: `eval/processing_debt/out/facts.jsonl` written; oracle spend ≤ 50 queries (verify cache dir has ≤ 50 files). If Brave free credits exhaust mid-run, it halts cleanly (cached partials preserved) — resume re-uses cache.

- [ ] **Step 2: Emit the adjudication CSV and hand-label 100%**

Run:
```bash
.venv/bin/python -c "
import json
from eval.processing_debt.types import FactRecord, PresenceResult
from eval.processing_debt.adjudicate import emit_csv
recs=[]
for l in open('eval/processing_debt/out/facts.jsonl'):
    d=json.loads(l); pr=d['presence']
    recs.append(FactRecord(d['question'],d['stratum'],d['fact_text'],d['vital'],d['guard_verdict'],
        d['in_answer'],PresenceResult(pr['present'],pr['probes_hit'],[]),d['fact_class'],d['stage'],d['xray_ref']))
emit_csv(recs,'eval/processing_debt/out/adjudicate.csv')
print('wrote adjudicate.csv —', len(recs), 'rows to label')
"
```
Then the owner + Claude fill `human_in_answer`, `human_presence`, `human_stage_ok` for every **vital** row (one focused session, ~200 nuggets).

- [ ] **Step 3: Compute κ and build the report**

Run:
```bash
.venv/bin/python -c "
import json
from eval.processing_debt.adjudicate import ingest_labels, cohen_kappa
from eval.processing_debt.report import build_report, render_md
from eval.processing_debt.types import FactRecord, PresenceResult
recs=[]
for l in open('eval/processing_debt/out/facts.jsonl'):
    d=json.loads(l); pr=d['presence']
    recs.append(FactRecord(d['question'],d['stratum'],d['fact_text'],d['vital'],d['guard_verdict'],
        d['in_answer'],PresenceResult(pr['present'],pr['probes_hit'],[]),d['fact_class'],d['stage'],d['xray_ref']))
labels=ingest_labels('eval/processing_debt/out/adjudicate.csv')
machine_in=[r.in_answer for r in recs if r.vital][:len(labels['in_answer'])]
machine_pr=[r.presence.present for r in recs if r.vital][:len(labels['presence'])]
kappas={'in_answer':cohen_kappa(machine_in,labels['in_answer']),
        'presence':cohen_kappa(machine_pr,labels['presence'])}
rep=build_report(recs,kappas)
open('eval/processing_debt/out/pilot_report.md','w').write(render_md(rep))
print(render_md(rep))
"
```
Expected: `pilot_report.md` with the headline debt %, per-stage table, κ, and SC1/SC4/SC5 verdicts. **SC1 (κ≥0.6) decides whether we're allowed to scale.**

- [ ] **Step 4: Run the full test suite green**

Run: `.venv/bin/python -m pytest v2/tests/processing_debt/ -v`
Expected: all pass.

- [ ] **Step 5: Commit the deliverable (report only; cache gitignored)**

```bash
printf ".cache/\n" > eval/processing_debt/.gitignore
git add eval/processing_debt/.gitignore eval/processing_debt/out/facts.jsonl eval/processing_debt/out/adjudicate.csv eval/processing_debt/out/pilot_report.md
git commit -m "feat(processing-debt): pilot run + adjudication + first Processing-Debt report"
```

---

## Self-Review (spec coverage)

- **G1 stage-attributed report** → Tasks 9, 13, 15. ✅
- **G2 exhaustive presence check** → Task 6 (kg/fts/embed/grep union). ✅
- **G3 instrument validation (100% adjudication + κ + oracle-correctness)** → Tasks 11, 15; oracle-correctness tally is the guard `DROPPED_ORACLE`/`we_are_authority` counts surfaced in the report (add a one-line rate to `render_md` during Task 13 if desired). ✅ *(minor: SC6 oracle-rate line is computed from records in report; SC2/SC3 are enforced as the Task-14 control gate, not in `build_report`.)*
- **G4 reuse frameworks** → entailment/nuggetize follow RAGChecker+AutoNuggetizer protocol; eRAG in Task 9; ARES scaffold = the adjudicate CSV shape (Task 11). ✅ *(deviation flagged in Global Constraints — local impl instead of the pip package; needs owner OK.)*
- **G5 ≤ $3, no bill risk** → oracle cache + 50-cap + "Free credits only" plan. ✅
- **G6 per-fact JSONL** → Task 12/15 `facts.jsonl`. ✅
- **ND1 distillation** → not built. ✅ **ND2 scale-up/ARES-run** → not built (only CSV scaffold). ✅ **ND3 no knowledge import** → no DB writes (Global Constraints). ✅ **ND4 web→no debt number** → web_needing facts are almost all `NOT_OWNED`; report denominator is owned vital only. ✅ **ND5 no prod fixes** → package imports only. ✅
- **Success criteria SC1–SC6:** SC1/SC4/SC5 in `build_report`; SC2/SC3 in the Task-14 control gate; SC6 oracle-rate surfaced in the report. ✅

**Type consistency:** `FactRecord`, `PresenceResult`, `XRay`, `Attribution` field names are used identically across Tasks 1/6/7/9/10/13/15. `entails(fact, text)`, `presence(conn, fact)`, `guard(nugget, oracle)`, `attribute(conn, fact, presence, xray)` signatures match between definition and callers.

**Open reconciliation items (handled in Task 14, not placeholders):** Brave `citations` JSON key; `Route` attribute names; `ask.sh` final-answer delimiter; sqlite-vec KNN SQL (`knowledge_vectors`/`embedding`/`MATCH k`). Each has a named fix location and a live-probe step.

---

# PLAN REVISION v2 (2026-07-06) — SUPERSEDES the above where noted

> Folds in: owner's **3×50 = 150-question** sourcing decision; the senior-eng review's six MUST-FIXES
> (M1–M6) + should-fixes; and Fable's two guardrails (A nugget-set validation, B three-way entailment).
> Each build subagent implements its task per the ORIGINAL task above **as amended here**. Where this
> section and the original conflict, THIS WINS. Companion review files in the same folder.

## R0. Question sourcing — 3 sets × 50 = 150 (supersedes design §5 / Task 12 STRATA)

Three independent sets, each 50, each with its own internal strata + controls. Reported separately AND
compared (real-vs-synthetic debt is a finding).

- **Set A — REAL student logs.** Source: `SELECT DISTINCT question_text, was_answered, confidence,
  matched_topic FROM questions WHERE question_text IS NOT NULL AND length(question_text) >= 12`.
  (908 distinct today.) Steps: (1) **junk filter** — drop < 3 tokens or obvious fragments; (2)
  **PII scan** — drop/redact any row containing an email or a student self-identifier before it goes
  outbound to Brave (faculty names are public, fine); (3) **dedup/cluster** near-paraphrases (embed +
  cosine ≥ 0.92 → keep one exemplar) — logs are head-heavy; (4) **stratify by the STORED signals** (no
  ask.sh needed): `answered_hi_conf` / `answered_lo_conf` / `deflected(was_answered=0)` /
  `abstained(mode/topic)`; (5) add the 5 positive + 3 oracle-blind controls.
- **Set B — SampleQuestions DB-answerable** (`docs/SampleQuestions/Question based on DB.txt`). Stratify by
  pipeline path via one `ask.sh` label pass (as original Task 12). Owned-knowledge debt.
- **Set C — SampleQuestions web-needing** (`docs/SampleQuestions/Questions based on internet.txt`).
  Mostly NOT_OWNED by construction → knowledge-gap + our-live-fallback-vs-Brave track (ND4). No debt
  denominator expected; report separately.

**Cost:** 150 × ~$0.057 ≈ **$8.55** (~$3.55 after $5 free credits; << $30). **Adjudication:** ~600 vital
nuggets ≈ ~3 sessions. Sets run/adjudicate/report **independently** so partial completion still yields a
usable Set-A number first (do Set A first — it's the real-demand headline).

**`sample.py` change:** replace single `STRATA`/`sample()` with `sample_set_a()`, `sample_set_b()`,
`sample_set_c()` each returning 50 `(question, stratum)` pairs; `run_pilot` takes a `set_name` and writes
`facts_{set}.jsonl`. Set-A loader queries the live `questions` table (read-only).

## R1. `oracle_brave.py` — multi-key + hard spend counter (should-fix + owner's 2nd-key note)

- Accept a **list** of keys: read `BRAVE_ANSWERS_API_KEY`, `BRAVE_ANSWERS_API_KEY_2`, … from `.env`; on a
  402/429 (credit/quota exhausted) rotate to the next key; raise only when all exhausted.
- **Hard spend guard:** module-level counter of *live* (non-cached) calls; `MAX_LIVE_CALLS` default 200;
  raise `RuntimeError` on overflow so a bug can't run away. Cache hits don't count.

## R2. Read-only enforcement (should-fix; Global Constraint made real) — `dbconn.py`

`get_ro_connection` MUST open a true read-only handle, not RW-by-discipline:
```python
import sqlite3, sqlite_vec
def get_ro_connection(db_path="gsa_gateway.db"):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True); sqlite_vec.load(conn); conn.enable_load_extension(False)
    return conn
```
**Acknowledge (do NOT try to prevent):** capturing answers via `ask.sh --answer` runs the live
`message_handler`, which INSERT+DELETEs one analytics `questions` row per question (trace_query.py:206-210)
— an ops-DB write matching eval.sh's accepted pattern. The "no writes" claim = no writes to KB/KG *content*;
the transient analytics row is expected. State this in the report's methods note.

## R3. Answer capture uses `LIVE_ENABLED=0` (should-fix — confound + uncounted spend) — `run_pilot.py`

Capture our production answer with the njit.edu live-fallback OFF, so a fact answered only from live web
never counts as `IN_ANSWER` (which would confound "surfaced from OUR KB/KG") and we spend no uncounted
Brave *Search* credits. Set `LIVE_ENABLED=0` in the subprocess env for the `ask.sh` call; also strip the
answer cleanly (take text AFTER the `FINAL LLM ANSWER … ` header line + its rule line, not just `split`).

## R4. Task 3 `entailment.py` — THREE-WAY verdict (Fable Guardrail B)

Replace the boolean judge with a three-way one, and give callers the correct lean:
```python
_SCHEMA = {"type":"object","properties":{"verdict":{"type":"string","enum":["yes","no","unsure"]}},
           "required":["verdict"]}
def entail_verdict(fact, text, *, gen=None) -> str:   # 'yes' | 'no' | 'unsure'
    gen = gen or _default_gen
    out = gen(_SYSTEM, f"CLAIM:\n{fact}\n\nTEXT:\n{text}\n\nIs the CLAIM supported by the TEXT?", _SCHEMA)
    v = (out or {}).get("verdict")
    return v if v in ("yes","no","unsure") else "no"   # fail-safe = no
```
**Per-caller lean (CRITICAL — wrong lean = under-report debt):**
- **IN_ANSWER** (classify): `unsure → treated as NOT in our answer` (`in_answer = verdict=='yes'`). Record
  `unsure` for the priority-review queue.
- **PRESENCE** (`presence_check.presence`): `unsure → treated as PRESENT` (conservative about declaring
  NOT_OWNED). `present = verdict in ('yes','unsure')`; tag `unsure`-only hits for human review.
- **ORACLE-GUARD** (citation support): `unsure → drop (unsupported)` — keep the guard strict.
- **eRAG** (chunk_yields_fact): `unsure → False` (don't credit a RANK save on a maybe).
Every FactRecord carries the raw verdicts so adjudication can see the `unsure`s. Keep a thin
`entails(fact,text)->bool = entail_verdict(...)=='yes'` only where a hard bool is genuinely wanted; do NOT
use it for PRESENCE.

## R5. Task 6 `presence_check.py` — CRUX FIXES M1/M2/M3 (all under-report bugs)

**M1 kg_probe span** — confirm against real structured content, not the bare name. Build the candidate
span from the node row AND its edges:
```python
def _node_span(conn, node_id):
    n = conn.execute("SELECT name, type, attrs FROM nodes WHERE id=?", (node_id,)).fetchone()
    parts = [n["name"] or "", n["type"] or "", n["attrs"] or ""]
    for e in conn.execute("SELECT type, category, attrs FROM edges WHERE src_id=? AND is_active=1", (node_id,)):
        parts += [e["type"] or "", e["category"] or "", e["attrs"] or ""]
    return " | ".join(p for p in parts if p)
```
Use `_node_span(conn, r["id"])` as the `PresenceEvidence.span` for kg_probe. Add a test that a title/office
fact (e.g. "Pan Xu's office is 4310 GITC") is PRESENT through kg_probe+entailment on the fixture.

**M2 embed_probe** — (a) build the Embedder from the ACTIVE DESCRIPTOR and assert width; (b) use the
production KNN SQL verbatim (mirror `retriever._semantic`, retriever.py:376-380):
```python
def _real_embed_and_knn():
    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.model_descriptor import active_descriptor
    import sqlite_vec
    emb = Embedder(); dim = active_descriptor().dim
    def embed_query(text):
        v = emb.embed_query(text)
        if v is not None and len(v) != dim:
            raise RuntimeError(f"embed width {len(v)} != active dim {dim}")
        return v
    def knn(conn, vec, k=100):
        try:
            rows = conn.execute(
                "SELECT ki.id, ki.type, ki.content FROM knowledge_vectors kv "
                "JOIN knowledge_items ki ON ki.id = kv.item_id "
                "WHERE kv.embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(vec), k)).fetchall()
        except Exception:
            return []
        return [(r[0], r[1], r[2]) for r in rows]
    return embed_query, knn
```
**T14 live assertion:** embed_probe MUST return ≥1 hit for a known paraphrase of a real corpus fact, else
the probe is dead — fail the gate. (Confirm the real vec table name/columns `knowledge_vectors(item_id,
embedding)` against schema.py during T14; adjust if different.)

**M3 grep_probe** — widen scope + real span. Grep over `content` AND `title` AND `nodes.attrs`; set the
evidence span to a window of the DB text around the match (not a slice of the fact). Also raise the
fts_probe span from `content[:300]` to a match-centered window (or full content) so support past char 300
isn't lost.

## R6. Task 9 attribution — M4 + ROUTER precision

**M4 exclude_types** — read the LIVE setting, don't hardcode:
```python
def _excluded_types(conn) -> set[str]:
    row = conn.execute("SELECT value FROM settings WHERE key='retriever.exclude_types'").fetchone()
    if row and row[0] is not None:
        return {t.strip() for t in row[0].split(",") if t.strip()}
    return {"publication", "syllabus"}   # real DEFAULT_EXCLUDE_TYPES (retriever.py:148)
```
(Confirm the `settings` table's column names during T14; adjust the SELECT if needed.)

**ROUTER branch precision** — only blame ROUTER when a KG-owned fact's *owning* structured skill wasn't
routed AND there is no servable `knowledge_item` chunk that could have carried it. If a servable ki chunk
exists, prefer the POOL/RANK/COMPOSE branch. (Prevents blaming ROUTER on every RAG query that merely also
has a node.)

## R7. Task 7 `xray.py` — production-fidelity (should-fix)

Build the retriever with the SAME config production's answer path uses (`group_by_entity=True`,
deep-fallback + office tier if the shim enables them) and take the fused pool at `limit >= 2 * pool_size`
(read `pool_size` from config; don't hardcode 50) so `cid not in fused_pool_ids` can't be a truncation
artifact. Drop `router_family` (Route is `Route(skill, args)` — no `.family`); keep only `router_skill`.

## R8. Task 11 `adjudicate.py` — KEY-BASED (Fable Guardrail A) + nugget-set validation

- **Stable fact id:** `fact_id = sha1(question + '␟' + fact_text)`. `emit_csv` writes `fact_id`; κ join is
  **by `fact_id`, not row position** (positional pairing breaks the moment a human rejects/adds a nugget).
- **Nugget-set validation (Guardrail A):** the CSV includes, per question, the machine's nugget list with
  a `human_nugget_ok` column (accept/reject) AND a free-text `human_missing_nuggets` field. Compute a
  **decompose-quality** number: nugget precision = accepted/total-machine; nugget recall =
  accepted/(accepted+human-added). Report alongside κ — this catches decompose failure that κ is blind to.
- `cohen_kappa` unchanged, but fed key-aligned machine/human lists (drop facts the human rejected;
  `unsure` rows counted per the R4 lean the machine used).

## R9. Task 13 `report.py` — SC6 (M5) + nugget-quality + per-set

- Add **oracle-correctness rate** = `(DROPPED_ORACLE + we_are_authority) / total_guarded`; render it; add
  **SC6 gate** = flag loudly if > 30%.
- Add the **nugget precision/recall** block from R8.
- Emit **per-set** reports (`pilot_report_A/B/C.md`) + a combined comparison table (debt by set).

## R10. Task 14 — control gate = a REAL HALT (harden SC2/SC3)

Replace the `print()` checks with an actual non-zero `sys.exit()` when SC2 (positive-control owned-misses
> 1) or SC3 (an oracle-blind fact not guard-flagged) fails — the driver for the full 150 must refuse to
run until the controls pass. Also make M6 (Brave Answers 200-OK probe) a hard precondition of the same gate.

## R11. Goals-coverage delta (amends the original self-review)
- G2 now COMPLETE (M1–M3 fix the probes). G3 now COMPLETE (M5 adds SC6 + nugget-quality). SC2/SC3 now real
  halts (R10). New: per-set comparison (R0/R9). Guardrails A/B folded (R4/R8). Everything else unchanged.

## R12. Build order note
Tasks 1–13 build as amended. Task 14 gate runs on Set A's controls FIRST. Then Set A full 50 →
adjudicate → report (real-demand headline). Then Sets B and C reuse the same code (only the sampler +
`facts_{set}.jsonl`/report vary). Stop after Set A if any SC1 (κ) fails — do not spend on B/C until the
instrument is proven.

## R13. Task 13 `report.py` — power analysis + cluster-robust CI (answers "how many do we need?")

Add `power_analysis(records) -> dict` and render it in every per-set report. Two pieces:

**(a) Cluster-robust 95% CI on the debt estimate** — nuggets are clustered within a question, so a naive
binomial CI overstates precision. Bootstrap over QUESTIONS (resample questions with replacement, recompute
debt each draw, take the 2.5/97.5 percentiles):
```python
def bootstrap_debt_ci(records, iters=2000, rng=None):
    import random; rng = rng or random.Random(0)
    by_q = {}
    for r in records:
        if r.vital and r.fact_class in ("IN_ANSWER", "OWNED_NOT_SURFACED"):
            by_q.setdefault(r.question, []).append(r.fact_class == "OWNED_NOT_SURFACED")
    qs = list(by_q)
    if not qs: return (0.0, 0.0, 0.0)
    def _debt(sample_qs):
        num = sum(sum(by_q[q]) for q in sample_qs)
        den = sum(len(by_q[q]) for q in sample_qs)
        return num / den if den else 0.0
    point = _debt(qs)
    draws = sorted(_debt([rng.choice(qs) for _ in qs]) for _ in range(iters))
    lo, hi = draws[int(0.025*iters)], draws[int(0.975*iters)]
    return (point, lo, hi)
```

**(b) Required-N back-solve** — from the observed debt `p` and the observed owned-vital yield per question
(`facts_per_q`), compute the sample size for a target margin `E` (overall and, using each stage's observed
share, per stage), and translate facts→questions:
```python
def required_n(p, facts_per_q, target_margin=0.05, z=1.96):
    if facts_per_q <= 0: return None
    n_facts = (z*z * p*(1-p)) / (target_margin*target_margin)   # owned-vital facts needed
    return {"facts_needed": round(n_facts), "questions_needed": round(n_facts / facts_per_q)}
```
Render, e.g.: *"Debt = 31% (95% CI 22–40%, cluster-bootstrap over 50 questions). For ±5% overall you need
~322 owned-vital facts ≈ ~130 questions (have 108 → ~22 more). For ±10% per stage on POOL (observed 34% of
misses) you need ~N more."* Compute the per-stage version by applying the same formula to each stage's
observed count/share so the owner reads the marginal cost of tighter per-stage numbers directly.

This makes the pilot self-answering on sample size: Set A's report states the CI AND the N required for any
target precision — the owner decides scale from measured numbers, not a guess.

## R14. Fable final-check fold (B1–B5 + unsure-rate) — MUST be in before SPENDING on Set A

B1–B5 are small, localized. B1/B2/B4/B5 gate *spending*; none block starting Tasks 1–13.

**B1 — guard-drop must be RECOVERABLE (else a wrongly-dropped true+owned fact silently under-reports debt).**
`adjudicate.py` `emit_csv`: also emit `DROPPED_ORACLE` facts with a `human_guard_ok` column (human can rescue
a fact the guard wrongly dropped because the oracle's *cited page* was only unsure-supporting even though the
fact is real and in our KB). Task 15 step-2 CSV must INCLUDE `DROPPED_ORACLE` rows (not just vital
IN_ANSWER/OWNED/NOT_OWNED). A rescued fact re-enters classification (presence-check + attribute).

**B2 — control gate runs on HUMAN-confirmed misses, not raw machine output.** The IN_ANSWER lean
(unsure→not-in-answer) can manufacture a false `OWNED_NOT_SURFACED` on a positive control we actually answer
perfectly (Granite merely unsure) → false SC2 `sys.exit()`. FIX (Task 14): adjudicate the 8 control questions'
facts FIRST (~40–60 nuggets, cheap), then run SC2 (≤1 positive-control owned-miss) / SC3 on the
HUMAN-confirmed set. Bonus: early κ read on controls before any spend.

**B3 — `required_n` must be cluster-consistent with R13a (else it understates questions needed).** The naive
`z²p(1-p)/E²` assumes fact independence — the exact thing R13a corrected. Apply a design effect
`DEFF = 1 + (m̄−1)·ICC` (m̄ = mean vital facts/question; ICC estimated from the data) to `n_facts`, OR derive
N_questions directly from the bootstrap width scaling (half-width ∝ 1/√N_questions). State that percentile CI
is approximate at the tails for n≈50 (BCa if cheap; percentile OK for a pilot).

**B4 — suppress the Set-C (low-denominator) debt headline.** In `report.py`, when
`denom (=IN_ANSWER+OWNED_NOT_SURFACED vital) < 20`, do NOT print a debt %; render
"insufficient owned-fact denominator — not a debt estimate" and asterisk it in the per-set comparison so
Set C's noise isn't placed next to A/B as comparable.

**B5 — verify RO-handle compatibility (Task 14 precondition).** R2 opens `file:…?mode=ro`. Confirm
`route(conn,…)`, `V2Retriever(conn,…).retrieve()`, and the FTS5/vec `MATCH` probes ALL execute under that
read-only handle with NO incidental write (some read paths do analytics/cache upserts → a RW-only op throws
on a ro handle). If any needs write, use a separate RW handle for THAT call only, keep probes on the ro
handle. (The `ask.sh` subprocess writes its own analytics via its own RW conn in a separate process — fine,
already acknowledged.)

**Minor — report the per-decision `unsure` RATE next to κ** (`report.py`). κ is computed on the post-lean
binary (correct), but a judge that only reaches good κ by leaning out of 40% unsure is fragile. Raw verdicts
are already stored — surface unsure-rate per decision as the real fragility signal.

# Kavosh Auto-Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An isolated, self-running harness that manufactures Codex-generated traffic against a read-only snapshot of Kavosh's DB, grades each answer with a separate deterministic checker, persists to `autoeval.db`, and produces a morning triage report — surviving Codex usage-windows unattended for weeks.

**Architecture:** A sampler reads ground truth from a frozen snapshot copy of the production KB DB. Codex generates three arms of questions (grounded / noisy / out-of-scope), each carrying a machine-checkable `expected` spec. A runner drives the full `handle()` pipeline against the snapshot and captures router metadata out-of-band via `unified_router.decide()`. A deterministic checker assigns pass/fail + one of three failure classes (fabrication > resolution_failure > routing_failure) plus a separate `data_gap` signal. Everything persists to a dedicated `autoeval.db`; a self-healing wrapper parses Codex's own reset time and auto-resumes.

**Tech Stack:** Python 3.11 (repo `.venv`), sqlite3 (+ sqlite-vec via the repo helpers), `codex exec` subprocess, Ollama (local, for the soft LLM-judge only), pytest.

## Global Constraints

- **Isolation:** all new code under `autoeval/`; the only file outside it is `scripts/autoeval.sh`. NO edits to any production module (`bot/`, `v2/`). NO memory-system files.
- **Production DB is never written.** The harness copies `gsa_gateway.db` to a snapshot and runs Kavosh against the copy. Ground-truth reads use a `mode=ro` connection.
- **Required env exports** (set by `scripts/autoeval.sh` BEFORE python starts; asserted at startup, fail-fast): `ROUTER_V21=1`, `ROUTER_V21_SHADOW=0`, `LIVE_ENABLED=0`, `ROUTER_V21_SLOT_RECOVERY=0`.
- **Codex never grades Kavosh.** The checker is deterministic. The LLM-judge (local Ollama) is a soft, separate signal, `graded_soft=1`, never part of pass/fail.
- **Arm C (out-of-scope) is mandatory** every run. `data_gap` is a separate signal, never a failure_class, never inflates routing bugs.
- **Interpreter:** run everything with `.venv/bin/python` (or `PYTHONPATH=$PWD .venv/bin/python`). Repo root: `/home/md724/gsa-gateway`.
- **Commits:** no Claude/co-author attribution (owner rule). Branch: `feat/autoeval-harness`.

## File Structure

```
autoeval/
  __init__.py
  config.py          AutoEvalConfig dataclass + assert_env() (env fail-fast) + paths
  store.py           autoeval.db schema (runs/questions/results/coverage) + typed CRUD
  snapshot.py        make_snapshot() -> (path, sha256); ro_connect()
  models.py          dataclasses: SourceItem, ExpectedSpec, GeneratedQuestion, KavoshObservation, CheckOutcome
  sampler.py         sample_items() + ground-truth extraction (reuses entity.py/skills.py)
  runner.py          KavoshRunner: build assistant on snapshot, run handle()+decide(), observe
  checker.py         typed checks + failure-class assignment + A/B pairing
  judge.py           soft Ollama LLM-judge for fuzzy prose
  codex_client.py    codex exec subprocess + rate-limit detection (adapted from teacher-eval)
  generator.py       3-arm Codex generation + expected-spec validation
  resilience.py      parse_reset_seconds + paused-status file + auto-resume loop
  report.py          triage report generator
  live.py            tail / status commands
  harness.py         main orchestration loop (one run window)
  tests/
    __init__.py
    test_config.py test_store.py test_snapshot.py test_sampler.py
    test_runner.py test_checker.py test_judge.py test_codex_client.py
    test_generator.py test_resilience.py test_report.py
scripts/
  autoeval.sh        launcher: env exports + subcommands (run / tail / status / smoke)
```

**Shared dataclasses (defined in Task 4, used everywhere — names are fixed):**

```python
# autoeval/models.py
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class ExpectedSpec:
    type: str                      # contact | count | metric | list | abstain_or_clarify | prose | entity
    item_key: str                  # family-typed: person nodes.key OR str(org_id) OR area string
    value: Optional[str] = None    # the expected value (email, number, etc.)
    must_contain_field: Optional[str] = None   # e.g. "email"
    members: list[str] = field(default_factory=list)   # for type=list
    skill_hint: Optional[str] = None
    missing_field: Optional[str] = None          # set when arm C targets a genuinely absent field

@dataclass
class SourceItem:
    item_type: str                 # person | org | area | chunk
    item_key: str                  # family-typed key (see ExpectedSpec.item_key)
    display_name: str
    ground_truth: dict[str, Any]
    has_fields: list[str]
    missing_fields: list[str]

@dataclass
class GeneratedQuestion:
    arm: str                       # answer | noisy | out_of_scope
    variant_type: Optional[str]    # typo | wording | esl | truncation (arm B only)
    twin_ref: Optional[str]        # arm B: a stable ref to its arm-A twin question_text
    question_text: str
    expected: ExpectedSpec
    item_type: str
    item_key: str
    codex_raw_ref: str             # path/hash of the stored raw codex response

@dataclass
class KavoshObservation:
    answer_text: str
    used_ai: bool
    is_live: bool
    is_deep: bool
    source_note: Optional[str]
    family: Optional[str]          # from decide(): KG|RAG|LIVE|CLARIFY|COMMAND|OTHER
    skill: Optional[str]
    resolved_key: Optional[str]    # family-aware: entity_id for person skills, str(org_id) for org
    slot_extracted: bool           # decide() went through LLM slot extraction (fidelity caveat)
    is_abstain: bool               # text matched a canned abstain string
    is_clarify: bool               # text matched the clarify string
    latency_ms: int

@dataclass
class CheckOutcome:
    result: str                    # pass | fail
    failure_class: Optional[str]   # None | fabrication | resolution_failure | routing_failure
    data_gap: bool
    evidence: dict[str, Any]
    graded_soft: bool = False
    llm_judge_verdict: Optional[str] = None
    llm_judge_confidence: Optional[float] = None
```

---

### Task 1: Package scaffold, config, and env fail-fast

**Files:**
- Create: `autoeval/__init__.py` (empty), `autoeval/tests/__init__.py` (empty)
- Create: `autoeval/config.py`
- Test: `autoeval/tests/test_config.py`

**Interfaces:**
- Produces: `AutoEvalConfig` dataclass (`repo_root`, `prod_db`, `snapshot_dir`, `autoeval_db`, `sampler_mix`, `arm_counts`, `concurrency`, `staleness_days`, `live_enabled`); `assert_env()` (raises `RuntimeError` if a required env var is wrong); `load_config()`.

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_config.py
import os
import pytest
from autoeval.config import assert_env, load_config, REQUIRED_ENV

def test_assert_env_passes_when_all_correct(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    assert_env()  # no raise

def test_assert_env_fails_on_wrong_flag(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ROUTER_V21_SHADOW", "1")  # wrong
    with pytest.raises(RuntimeError, match="ROUTER_V21_SHADOW"):
        assert_env()

def test_load_config_defaults():
    cfg = load_config()
    assert cfg.arm_counts["answer"] == 3
    assert cfg.arm_counts["out_of_scope"] == 2
    assert abs(sum(cfg.sampler_mix.values()) - 1.0) < 1e-6
    assert cfg.concurrency == 1
    assert cfg.autoeval_db.endswith("autoeval.db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_config.py -v`
Expected: FAIL (module `autoeval.config` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/config.py
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path("/home/md724/gsa-gateway")

# Env vars the harness REQUIRES (read at import time by bot.config / message_handler).
# Values are the harness-correct settings; the launcher exports them before python starts.
REQUIRED_ENV = {
    "ROUTER_V21": "1",            # else unified_router is None -> runner AttributeError
    "ROUTER_V21_SHADOW": "0",     # else handle() ignores decide() + writes shared shadow log
    "LIVE_ENABLED": "0",          # module constant; must be set pre-import (zero external footprint)
    "ROUTER_V21_SLOT_RECOVERY": "0",  # deterministic captured route
}

def assert_env() -> None:
    wrong = {k: (os.environ.get(k), v) for k, v in REQUIRED_ENV.items()
             if os.environ.get(k) != v}
    if wrong:
        lines = [f"  {k}={got!r} (must be {want!r})" for k, (got, want) in wrong.items()]
        raise RuntimeError(
            "autoeval required env not set correctly (export via scripts/autoeval.sh):\n"
            + "\n".join(lines))

@dataclass
class AutoEvalConfig:
    repo_root: Path = REPO_ROOT
    prod_db: str = str(REPO_ROOT / "gsa_gateway.db")
    snapshot_dir: str = str(REPO_ROOT / "autoeval" / "snapshots")
    autoeval_db: str = str(REPO_ROOT / "autoeval" / "autoeval.db")
    status_file: str = str(REPO_ROOT / "autoeval" / "status.json")
    sampler_mix: dict = field(default_factory=lambda: {
        "person": 0.50, "org": 0.20, "area": 0.15, "chunk": 0.15})
    arm_counts: dict = field(default_factory=lambda: {"answer": 3, "out_of_scope": 2})
    concurrency: int = 1
    staleness_days: int = 7
    live_enabled: bool = False
    codex_model: str | None = None  # None -> codex default

def load_config() -> AutoEvalConfig:
    cfg = AutoEvalConfig()
    Path(cfg.snapshot_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.autoeval_db).parent.mkdir(parents=True, exist_ok=True)
    return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/__init__.py autoeval/tests/__init__.py autoeval/config.py autoeval/tests/test_config.py
git commit -m "feat(autoeval): package scaffold + config + env fail-fast"
```

---

### Task 2: Results DB (`autoeval.db`) schema + store

**Files:**
- Create: `autoeval/store.py`
- Test: `autoeval/tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Store` class with `__init__(db_path)`, `init_schema()`, `create_run(...) -> run_id`, `insert_question(run_id, GeneratedQuestion) -> q_id`, `insert_result(q_id, CheckOutcome, KavoshObservation)`, `bump_coverage(item_key)`, `least_tested_keys(limit, item_type) -> list[str]`, `results_for_run(run_id) -> list[dict]`, `prev_run_at_commit(commit, before_run_id) -> Optional[int]`. (`GeneratedQuestion`/`CheckOutcome`/`KavoshObservation` are defined in Task 4's `models.py`; for this task use plain dicts/kwargs so Task 2 has no dependency on Task 4 — see Step 3 signatures.)

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_store.py
import tempfile, os
from autoeval.store import Store

def _store():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    s = Store(path); s.init_schema(); return s

def test_run_question_result_roundtrip():
    s = _store()
    run_id = s.create_run(db_snapshot_hash="abc", config_json="{}",
                          codex_model="gpt-5-codex", kavosh_commit="deadbeef", live_enabled=False)
    q_id = s.insert_question(run_id, item_type="person", item_key="crawler/x", arm="answer",
                             variant_type=None, twin_ref=None, question_text="q?",
                             expected_json='{"type":"contact"}', codex_raw_ref="raw/1")
    s.insert_result(q_id, answer_text="a", metadata_json="{}", result="pass",
                    failure_class=None, data_gap=False, evidence_json="{}", latency_ms=12,
                    resolved_entity_id="crawler/x", family="KG", skill="contact_of_person",
                    used_ai=False, graded_soft=False, llm_judge_verdict=None,
                    llm_judge_confidence=None)
    rows = s.results_for_run(run_id)
    assert len(rows) == 1 and rows[0]["result"] == "pass" and rows[0]["arm"] == "answer"

def test_coverage_bump_and_least_tested():
    s = _store()
    s.bump_coverage("k1"); s.bump_coverage("k1"); s.bump_coverage("k2")
    least = s.least_tested_keys(limit=1)
    assert least == ["k2"]  # k2 tested once, k1 twice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_store.py -v`
Expected: FAIL (no module `autoeval.store`).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/store.py
from __future__ import annotations
import sqlite3, time
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL, db_snapshot_hash TEXT, config_json TEXT,
  codex_model TEXT, kavosh_commit TEXT, live_enabled INTEGER);
CREATE TABLE IF NOT EXISTS questions (
  q_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, item_type TEXT, item_key TEXT, arm TEXT, variant_type TEXT,
  twin_ref TEXT, question_text TEXT, expected_json TEXT, codex_raw_ref TEXT);
CREATE TABLE IF NOT EXISTS results (
  q_id INTEGER PRIMARY KEY, answer_text TEXT, metadata_json TEXT,
  result TEXT, failure_class TEXT, data_gap INTEGER, evidence_json TEXT,
  latency_ms INTEGER, resolved_entity_id TEXT, family TEXT, skill TEXT,
  used_ai INTEGER, graded_soft INTEGER, llm_judge_verdict TEXT, llm_judge_confidence REAL);
CREATE TABLE IF NOT EXISTS coverage (
  item_key TEXT PRIMARY KEY, times_tested INTEGER DEFAULT 0, last_tested_at REAL);
"""

class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA); self.conn.commit()

    def create_run(self, *, db_snapshot_hash, config_json, codex_model,
                   kavosh_commit, live_enabled) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs(started_at,db_snapshot_hash,config_json,codex_model,"
            "kavosh_commit,live_enabled) VALUES(?,?,?,?,?,?)",
            (time.time(), db_snapshot_hash, config_json, codex_model, kavosh_commit,
             int(bool(live_enabled))))
        self.conn.commit(); return cur.lastrowid

    def insert_question(self, run_id, *, item_type, item_key, arm, variant_type,
                        twin_ref, question_text, expected_json, codex_raw_ref) -> int:
        cur = self.conn.execute(
            "INSERT INTO questions(run_id,item_type,item_key,arm,variant_type,twin_ref,"
            "question_text,expected_json,codex_raw_ref) VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, item_type, item_key, arm, variant_type, twin_ref, question_text,
             expected_json, codex_raw_ref))
        self.conn.commit(); return cur.lastrowid

    def insert_result(self, q_id, *, answer_text, metadata_json, result, failure_class,
                      data_gap, evidence_json, latency_ms, resolved_entity_id, family,
                      skill, used_ai, graded_soft, llm_judge_verdict, llm_judge_confidence):
        self.conn.execute(
            "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (q_id, answer_text, metadata_json, result, failure_class, int(bool(data_gap)),
             evidence_json, latency_ms, resolved_entity_id, family, skill, int(bool(used_ai)),
             int(bool(graded_soft)), llm_judge_verdict, llm_judge_confidence))
        self.conn.commit()

    def bump_coverage(self, item_key: str):
        self.conn.execute(
            "INSERT INTO coverage(item_key,times_tested,last_tested_at) VALUES(?,1,?) "
            "ON CONFLICT(item_key) DO UPDATE SET times_tested=times_tested+1,last_tested_at=?",
            (item_key, time.time(), time.time()))
        self.conn.commit()

    def least_tested_keys(self, limit: int, item_type: str | None = None) -> list[str]:
        rows = self.conn.execute(
            "SELECT item_key FROM coverage ORDER BY times_tested ASC, last_tested_at ASC "
            "LIMIT ?", (limit,)).fetchall()
        return [r["item_key"] for r in rows]

    def results_for_run(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT q.*, r.* FROM questions q LEFT JOIN results r ON r.q_id=q.q_id "
            "WHERE q.run_id=?", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def prev_run_at_commit(self, commit: str, before_run_id: int) -> Optional[int]:
        row = self.conn.execute(
            "SELECT run_id FROM runs WHERE kavosh_commit=? AND run_id<? "
            "ORDER BY run_id DESC LIMIT 1", (commit, before_run_id)).fetchone()
        return row["run_id"] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_store.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/store.py autoeval/tests/test_store.py
git commit -m "feat(autoeval): autoeval.db schema + store CRUD"
```

---

### Task 3: Snapshot module (frozen read-only copy of production DB)

**Files:**
- Create: `autoeval/snapshot.py`
- Test: `autoeval/tests/test_snapshot.py`

**Interfaces:**
- Produces: `make_snapshot(prod_db, snapshot_dir) -> (snapshot_path, sha256_hash)`; `ro_connect(db_path) -> sqlite3.Connection` (opens `file:...?mode=ro`, loads sqlite-vec).

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_snapshot.py
import sqlite3, tempfile, os
from autoeval.snapshot import make_snapshot, ro_connect

def _tiny_db():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p); c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES(1)")
    c.commit(); c.close(); return p

def test_make_snapshot_copies_and_hashes():
    src = _tiny_db(); d = tempfile.mkdtemp()
    path, h = make_snapshot(src, d)
    assert os.path.exists(path) and len(h) == 64
    assert sqlite3.connect(path).execute("SELECT x FROM t").fetchone()[0] == 1

def test_ro_connect_is_readonly():
    src = _tiny_db(); d = tempfile.mkdtemp()
    path, _ = make_snapshot(src, d)
    conn = ro_connect(path)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES(2)")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_snapshot.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/snapshot.py
from __future__ import annotations
import hashlib, shutil, sqlite3
from pathlib import Path

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def make_snapshot(prod_db: str, snapshot_dir: str) -> tuple[str, str]:
    """Copy the production DB to snapshot_dir/snap_<shorthash>.db. Returns (path, full_sha256).
    The hash is computed AFTER copy so it identifies the exact frozen bytes Kavosh ran against."""
    Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
    src_hash = _sha256(prod_db)
    dest = str(Path(snapshot_dir) / f"snap_{src_hash[:12]}.db")
    shutil.copyfile(prod_db, dest)
    return dest, _sha256(dest)

def ro_connect(db_path: str) -> sqlite3.Connection:
    """Read-only connection for ground-truth reads. sqlite-vec loaded so vec tables are visible;
    pure SELECTs on nodes/edges/knowledge_items don't need it but loading is harmless."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec  # noqa
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # ground-truth SELECTs don't require vec
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_snapshot.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/snapshot.py autoeval/tests/test_snapshot.py
git commit -m "feat(autoeval): snapshot copy + read-only connection helper"
```

---

### Task 4: Shared models + item sampler with ground-truth extraction

**Files:**
- Create: `autoeval/models.py` (the dataclasses block from the File Structure section — copy it verbatim)
- Create: `autoeval/sampler.py`
- Test: `autoeval/tests/test_sampler.py`

**Interfaces:**
- Consumes: `ro_connect` (Task 3); reused prod functions `entity.contact_of_person/title_of_person/research_of_person/metric_of_person/person_attrs` and `skills.resolve_org/people_in_org`.
- Produces: `models.py` dataclasses (`SourceItem`, `ExpectedSpec`, `GeneratedQuestion`, `KavoshObservation`, `CheckOutcome`); `extract_person(conn, key) -> SourceItem`; `sample_items(conn, mix, n, store) -> list[SourceItem]`.

- [ ] **Step 1: Create `autoeval/models.py`**

Copy the entire dataclasses block from the **File Structure** section above into `autoeval/models.py`. (No test needed for pure dataclasses; they're exercised by every downstream test.)

- [ ] **Step 2: Write the failing test**

```python
# autoeval/tests/test_sampler.py
import sqlite3, tempfile, os, json
from autoeval.sampler import extract_person

def _person_db():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE nodes(id INTEGER PRIMARY KEY, type TEXT, key TEXT, name TEXT,
                         attrs TEXT, is_active INT);
      CREATE TABLE edges(id INTEGER PRIMARY KEY, src_id INT, dst_id INT, type TEXT,
                         category TEXT, attrs TEXT, is_active INT);
    """)
    c.execute("INSERT INTO nodes VALUES(1,'Person','crawler/jane-doe','Doe, Jane',?,1)",
              (json.dumps({"email": "jdoe@njit.edu", "office": "GITC 4000"}),))
    c.commit(); c.close(); return p

def test_extract_person_fields_and_gaps():
    p = _person_db()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    item = extract_person(conn, "crawler/jane-doe")
    assert item.item_type == "person" and item.item_key == "crawler/jane-doe"
    assert item.ground_truth["email"] == "jdoe@njit.edu"
    assert "email" in item.has_fields and "office" in item.has_fields
    assert "phone" in item.missing_fields   # not on the node -> data-gap fuel
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_sampler.py -v`
Expected: FAIL (no module `autoeval.sampler`).

- [ ] **Step 4: Write minimal implementation**

```python
# autoeval/sampler.py
from __future__ import annotations
import random, sqlite3, sys
from pathlib import Path
from autoeval.models import SourceItem

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval import entity as _entity   # reused, read-only
from v2.core.retrieval import skills as _skills

_PERSON_CONTACT = ("email", "phone", "office")

def extract_person(conn: sqlite3.Connection, key: str) -> SourceItem:
    contact = _entity.contact_of_person(conn, key)      # {name,email,phone,office,present}
    titles = _entity.title_of_person(conn, key)["titles"]  # [(title, org)]
    research = _entity.research_of_person(conn, key)["areas"]
    attrs = _entity.person_attrs(conn, key)
    scholar = ((attrs.get("profiles") or {}).get("scholar") or {})
    gt = {"name": contact["name"], "email": contact["email"], "phone": contact["phone"],
          "office": contact["office"], "titles": titles, "research_areas": research,
          "scholar": {k: scholar.get(k) for k in ("citations", "h_index", "i10_index")}}
    has = list(contact["present"])
    if titles: has.append("titles")
    if research: has.append("research_areas")
    if any(gt["scholar"].values()): has.append("scholar")
    all_fields = list(_PERSON_CONTACT) + ["titles", "research_areas", "scholar"]
    missing = [f for f in all_fields if f not in has]
    return SourceItem(item_type="person", item_key=key, display_name=contact["name"],
                      ground_truth=gt, has_fields=has, missing_fields=missing)

def extract_org(conn: sqlite3.Connection, org_id: int) -> SourceItem:
    row = conn.execute("SELECT name,type,metadata FROM organizations WHERE id=?",
                       (org_id,)).fetchone()
    import json
    meta = json.loads(row["metadata"]) if row and row["metadata"] else {}
    members = _skills.people_in_org(conn, org_id)        # [(name,title,email)]
    gt = {"name": row["name"], "type": row["type"], "aliases": meta.get("aliases", []),
          "members": [m[0] for m in members]}
    has = ["name", "type"] + (["aliases"] if gt["aliases"] else []) + (["members"] if members else [])
    missing = [f for f in ("aliases", "members") if f not in has]
    return SourceItem(item_type="org", item_key=str(org_id), display_name=row["name"],
                      ground_truth=gt, has_fields=has, missing_fields=missing)

def _person_keys(conn, limit):
    return [r["key"] for r in conn.execute(
        "SELECT key FROM nodes WHERE type='Person' AND is_active=1 ORDER BY key LIMIT ?",
        (limit,)).fetchall()]

def _org_ids(conn, limit):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM organizations WHERE is_active=1 ORDER BY id LIMIT ?", (limit,)).fetchall()]

def sample_items(conn: sqlite3.Connection, mix: dict, n: int,
                 prefer_keys: list[str] | None = None, seed: int | None = None) -> list[SourceItem]:
    """Sample n items across types by `mix`. `prefer_keys` (from coverage) biases toward
    least-tested items so a long run sweeps the whole DB. Person + Org implemented here;
    area/chunk fall back to person until their extractors land (see Task 4b note)."""
    rng = random.Random(seed)
    out: list[SourceItem] = []
    n_person = max(1, int(round(n * mix.get("person", 0.5))))
    n_org = int(round(n * mix.get("org", 0.2)))
    pkeys = _person_keys(conn, 5000)
    if prefer_keys:
        pref = [k for k in prefer_keys if k in set(pkeys)]
        pkeys = pref + [k for k in pkeys if k not in set(pref)]
    else:
        rng.shuffle(pkeys)
    for k in pkeys[:n_person]:
        out.append(extract_person(conn, k))
    oids = _org_ids(conn, 2000); rng.shuffle(oids)
    for oid in oids[:n_org]:
        out.append(extract_org(conn, oid))
    return out
```

> **Task 4b note (fold into this task, no separate commit needed):** `area` and `chunk` extractors are deferred to a follow-up; the `sampler_mix` still lists them but `sample_items` currently draws only person+org. Add a `# DEFERRED: area/chunk extractors` comment above `sample_items` so the gap is loud, not silent. (Person 50% + Org 20% already covers the two families whose resolution check is fully specified.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_sampler.py -v`
Expected: PASS.

- [ ] **Step 6: Manual validation on real data (build-order step 1 gate)**

Run:
```bash
PYTHONPATH=$PWD .venv/bin/python -c "
from autoeval.snapshot import make_snapshot, ro_connect
from autoeval.sampler import sample_items
from autoeval.config import load_config
cfg = load_config()
path, h = make_snapshot(cfg.prod_db, cfg.snapshot_dir)
conn = ro_connect(path)
for it in sample_items(conn, cfg.sampler_mix, 10, seed=1):
    print(it.item_type, it.item_key, 'HAS', it.has_fields, 'MISSING', it.missing_fields)
"
```
Expected: 10 items printed; eyeball that `has_fields`/`missing_fields` look right for a few people you know (e.g. someone with an email shows `email` in has_fields).

- [ ] **Step 7: Commit**

```bash
git add autoeval/models.py autoeval/sampler.py autoeval/tests/test_sampler.py
git commit -m "feat(autoeval): shared models + person/org sampler with ground-truth + missing_fields"
```

---

### Task 5: Kavosh runner (drive handle() on the snapshot + capture metadata)

**Files:**
- Create: `autoeval/runner.py`
- Test: `autoeval/tests/test_runner.py`

**Interfaces:**
- Consumes: `assert_env` (Task 1); `models.KavoshObservation` (Task 4); reused `bot.core.assistant.build_assistant`, `bot.core.message_handler.MessageRequest`.
- Produces: `KavoshRunner` with `async build(snapshot_path)`, `async observe(question_text) -> KavoshObservation`, `async close()`; module fns `detect_abstain(text) -> bool`, `detect_clarify(text) -> bool`, `resolved_key_for(decision) -> (key, slot_extracted)`.

- [ ] **Step 1: Write the failing test** (pure helpers — no GPU/Ollama needed)

```python
# autoeval/tests/test_runner.py
from autoeval.runner import detect_abstain, detect_clarify, resolved_key_for

class _D:  # stand-in for RouteDecision
    def __init__(self, family, skill, args): self.family, self.skill, self.args = family, skill, args

def test_detect_abstain_matches_canned():
    assert detect_abstain("I wasn't able to find specific information about that in the GSA knowledge base.")
    assert detect_abstain("I wasn't able to find a specific answer to that in the GSA knowledge base.")
    assert not detect_abstain("Jane Doe's email is jdoe@njit.edu")

def test_detect_clarify():
    assert detect_clarify("I want to make sure I answer the right thing — could you rephrase")
    assert not detect_clarify("Here is the answer.")

def test_resolved_key_person_vs_org():
    k, slot = resolved_key_for(_D("KG", "contact_of_person", {"entity_id": "crawler/x"}))
    assert k == "crawler/x" and slot is False
    k2, _ = resolved_key_for(_D("KG", "people_in_org", {"org_id": 42}))
    assert k2 == "42"
    k3, _ = resolved_key_for(_D("RAG", None, {}))
    assert k3 is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_runner.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/runner.py
from __future__ import annotations
import sys, time
from pathlib import Path
from autoeval.config import assert_env
from autoeval.models import KavoshObservation

REPO = Path("/home/md724/gsa-gateway")
sys.path.insert(0, str(REPO))

# Canned strings copied verbatim from bot/core/message_handler.py (:43-44, :58, :640).
_ABSTAIN_SUBSTRINGS = (
    "I wasn't able to find specific information about that",
    "I wasn't able to find a specific answer to that",
)
_CLARIFY_SUBSTRING = "I want to make sure I answer the right thing"

# Person-centric skills expose args["entity_id"]; org skills expose args["org_id"].
_ORG_SKILLS = {"people_in_org", "officers_in_org", "faculty_in_department",
               "orgs_by_type", "areas_in_org", "area_counts", "count_people_by_research_area",
               "top_people_by_metric", "people_by_research_area"}

def detect_abstain(text: str) -> bool:
    return any(s in (text or "") for s in _ABSTAIN_SUBSTRINGS)

def detect_clarify(text: str) -> bool:
    return _CLARIFY_SUBSTRING in (text or "")

def resolved_key_for(decision) -> tuple[str | None, bool]:
    """Family-aware resolved key. Person skills -> entity_id; org skills -> str(org_id).
    slot_extracted flags routes that went through LLM slot extraction (fidelity caveat)."""
    args = getattr(decision, "args", {}) or {}
    slot = bool(args.get("_slot_extracted"))
    skill = getattr(decision, "skill", None)
    if args.get("entity_id"):
        return str(args["entity_id"]), slot
    if skill in _ORG_SKILLS and args.get("org_id") is not None:
        return str(args["org_id"]), slot
    return None, slot

class KavoshRunner:
    def __init__(self, config):
        assert_env()  # fail fast if ROUTER_V21 / SHADOW / LIVE / SLOT_RECOVERY are wrong
        self._botcfg = None; self._asst = None; self._handler = None

    async def build(self, snapshot_path: str):
        from bot.config import config as botcfg
        botcfg.database_path = snapshot_path                 # retriever seam (assistant.py:114)
        from bot.services.database import Database
        from bot.services.knowledge_base import KnowledgeBase
        from bot.services.moderation import RateLimiter
        from bot.core.assistant import build_assistant
        db = Database(snapshot_path)                          # combined mode; NO ops_db_path
        db.connect(); db.init_tables(); db.migrate_rag_columns()
        kb = KnowledgeBase(data_dir=botcfg.data_dir); kb.load()
        rl = RateLimiter(max_calls=10**9, period_seconds=1)
        self._botcfg = botcfg
        self._asst = await build_assistant(botcfg, db, kb, rl)
        self._handler = self._asst.message_handler
        assert self._handler.unified_router is not None, "ROUTER_V21 not active"

    async def observe(self, question_text: str) -> KavoshObservation:
        from bot.core.message_handler import MessageRequest
        import uuid
        decision = self._handler.unified_router.decide(question_text)  # sync, side-effect-free
        key, slot = resolved_key_for(decision)
        t0 = time.time()
        resp = await self._handler.handle(MessageRequest(
            user_id=f"autoeval::{uuid.uuid4().hex}", text=question_text, platform="telegram"))
        latency = int((time.time() - t0) * 1000)
        text = (resp.text or "").strip()
        return KavoshObservation(
            answer_text=text, used_ai=bool(resp.used_ai),
            is_live=bool(getattr(resp, "is_live", False)),
            is_deep=bool(getattr(resp, "is_deep", False)), source_note=resp.source_note,
            family=getattr(decision, "family", None), skill=getattr(decision, "skill", None),
            resolved_key=key, slot_extracted=slot,
            is_abstain=detect_abstain(text), is_clarify=detect_clarify(text), latency_ms=latency)

    async def close(self):
        if self._asst and getattr(self._asst, "embedder", None):
            await self._asst.embedder.close()
        if self._asst and getattr(self._asst, "ollama", None):
            try: await self._asst.ollama.close()
            except Exception: pass
```

> Note: `_slot_extracted` is not a real arg key today; `resolved_key_for` reads it defensively so the field exists in the observation. With `ROUTER_V21_SLOT_RECOVERY=0` (Task 1 env), slot extraction is off, so `slot_extracted` is `False` in practice — the caveat handling is future-proofing, not a live path.

- [ ] **Step 4: Run test to verify it passes** (helpers only)

Run: `.venv/bin/python -m pytest autoeval/tests/test_runner.py -v`
Expected: PASS (3 tests). (The async `build/observe` are integration-tested in Task 15 against a real snapshot + Ollama.)

- [ ] **Step 5: Commit**

```bash
git add autoeval/runner.py autoeval/tests/test_runner.py
git commit -m "feat(autoeval): Kavosh runner — handle() on snapshot + out-of-band decide() capture"
```

---

### Task 6: Deterministic checker — typed checks

**Files:**
- Create: `autoeval/checker.py`
- Test: `autoeval/tests/test_checker.py`

**Interfaces:**
- Consumes: `models.ExpectedSpec/KavoshObservation/CheckOutcome`; reused `v2.core.retrieval.faithfulness._norm`.
- Produces: `value_present(answer, value) -> bool`; `numeric_match(answer, value) -> bool`; `list_overlap(answer, members) -> (precision, recall)`; `check_typed(expected, obs) -> Optional[bool]` (True=correct, False=incorrect/contradiction, None=no typed check applies → prose).

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_checker.py
from autoeval.models import ExpectedSpec, KavoshObservation
from autoeval.checker import value_present, numeric_match, list_overlap

def _obs(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

def test_value_present_markdown_and_case_insensitive():
    assert value_present("Her email is **JDOE@njit.edu**.", "jdoe@njit.edu")
    assert not value_present("Her email is someoneelse@njit.edu.", "jdoe@njit.edu")

def test_numeric_match():
    assert numeric_match("He has 1,234 citations.", "1234")
    assert not numeric_match("He has 999 citations.", "1234")

def test_list_overlap_precision_recall():
    p, r = list_overlap("Members: Alice, Bob, Carol", ["Alice", "Bob", "Dan"])
    assert r == 2/3  # Alice+Bob found of 3 expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_checker.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/checker.py
from __future__ import annotations
import re, sys
from pathlib import Path
from autoeval.models import ExpectedSpec, KavoshObservation, CheckOutcome

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval.faithfulness import _norm  # markdown/whitespace/casing-safe normalizer

def value_present(answer: str, value: str) -> bool:
    """All normalized tokens of the expected value appear in the normalized answer.
    Reuses faithfulness._norm so markdown ** and casing don't break the match (the WS4 fix)."""
    a_tokens = set(_norm(answer).split())
    v_tokens = _norm(value).split()
    if not v_tokens:
        return False
    return all(t in a_tokens for t in v_tokens)

def numeric_match(answer: str, value: str) -> bool:
    want = re.sub(r"[,\s]", "", str(value))
    nums = re.findall(r"\d[\d,]*", answer)
    return any(re.sub(r"[,\s]", "", n) == want for n in nums)

def list_overlap(answer: str, members: list[str]) -> tuple[float, float]:
    a = _norm(answer)
    found = [m for m in members if all(t in a for t in _norm(m).split())]
    recall = len(found) / len(members) if members else 0.0
    precision = 1.0 if found else 0.0  # coarse; recall is the meaningful signal for a roster
    return precision, recall

def check_typed(expected: ExpectedSpec, obs: KavoshObservation) -> bool | None:
    """True=answer correct, False=incorrect/contradiction, None=no typed check (prose -> soft judge)."""
    t = expected.type
    if t in ("contact", "entity") and expected.value:
        return value_present(obs.answer_text, expected.value)
    if t in ("count", "metric") and expected.value:
        return numeric_match(obs.answer_text, expected.value)
    if t == "list":
        _, recall = list_overlap(obs.answer_text, expected.members)
        return recall >= 0.6
    return None  # prose / abstain handled by the failure-class layer (Task 7)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_checker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/checker.py autoeval/tests/test_checker.py
git commit -m "feat(autoeval): deterministic typed checks (contact/count/list) reusing WS4 _norm"
```

---

### Task 7: Failure-class assignment + A/B pairing

**Files:**
- Modify: `autoeval/checker.py` (add `classify(expected, obs, twin_passed) -> CheckOutcome`)
- Test: `autoeval/tests/test_checker.py` (add classification tests)

**Interfaces:**
- Consumes: `check_typed` (Task 6); `models.CheckOutcome`.
- Produces: `classify(expected: ExpectedSpec, obs: KavoshObservation, arm: str, missing_fields: list[str], twin_passed: Optional[bool]) -> CheckOutcome`.

- [ ] **Step 1: Write the failing test**

```python
# add to autoeval/tests/test_checker.py
from autoeval.checker import classify
from autoeval.models import ExpectedSpec, KavoshObservation

def _o(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

def test_armC_confident_answer_is_fabrication():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/zzyzx")
    obs = _o("Professor Zzyzx's email is zzyzx@njit.edu", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "fabrication"

def test_armC_correct_abstain_passes():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/zzyzx")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "pass" and out.failure_class is None

def test_missing_field_correct_abstain_is_data_gap_not_routing():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/x", missing_field="phone")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=["phone"], twin_passed=None)
    assert out.result == "pass" and out.data_gap is True and out.failure_class is None

def test_armA_contradiction_is_fabrication():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu",
                       must_contain_field="email")
    obs = _o("Her email is wrong@njit.edu")   # asserts a value, but not the truth
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "fabrication"

def test_armA_miss_is_routing_failure():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "routing_failure"

def test_armB_fail_with_passing_twin_is_resolution_failure():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="noisy", missing_fields=[], twin_passed=True)
    assert out.failure_class == "resolution_failure"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_checker.py -v`
Expected: FAIL (`classify` not defined).

- [ ] **Step 3: Write minimal implementation** (append to `autoeval/checker.py`)

```python
def _asserts_a_value(obs: KavoshObservation) -> bool:
    """Arm-C fabrication test: the answer is NOT a canned deflection AND makes an affirmative
    factual assertion (email / phone / number / an entity-card-style 'X is/are ...')."""
    if obs.is_abstain or obs.is_clarify:
        return False
    t = obs.answer_text
    if re.search(r"[\w.+-]+@[\w-]+\.\w+", t):      # an email
        return True
    if re.search(r"\d{3}[.\-\s]?\d{3}[.\-\s]?\d{4}", t):  # a phone
        return True
    if re.search(r"\b\d[\d,]*\b", t) and len(t) < 400:    # a bare figure in a short answer
        return True
    return len(t) > 40   # a substantive prose answer to a should-abstain question

def classify(expected: ExpectedSpec, obs: KavoshObservation, arm: str,
             missing_fields: list[str], twin_passed: bool | None) -> CheckOutcome:
    ev = {"expected_type": expected.type, "expected_value": expected.value,
          "answer_snippet": obs.answer_text[:240], "family": obs.family, "skill": obs.skill,
          "resolved_key": obs.resolved_key, "is_abstain": obs.is_abstain}
    field_missing = bool(expected.missing_field and expected.missing_field in missing_fields)

    # --- Arm C: should abstain/clarify ---
    if expected.type == "abstain_or_clarify" or arm == "out_of_scope":
        if obs.is_abstain or obs.is_clarify:
            return CheckOutcome("pass", None, field_missing, ev)  # correct abstain (maybe data_gap)
        if _asserts_a_value(obs):
            ev["check"] = "armC_assertion"
            return CheckOutcome("fail", "fabrication", field_missing, ev)
        return CheckOutcome("pass", None, field_missing, ev)

    # --- Arm A/B: should answer ---
    typed = check_typed(expected, obs)
    ev["check"] = f"typed:{expected.type}"
    if typed is None:
        # prose/fuzzy -> soft judge decides; hard result is a provisional pass, graded_soft set later
        return CheckOutcome("pass", None, field_missing, ev, graded_soft=True)
    if typed is True:
        return CheckOutcome("pass", None, field_missing, ev)
    # typed is False: either a contradiction (asserted a wrong value) or a miss (abstained/absent)
    if _asserts_a_value(obs):
        ev["check"] = "contradiction"
        return CheckOutcome("fail", "fabrication", field_missing or False, ev)
    # a miss (no value asserted). Arm B whose clean twin passed -> resolution broke on noise.
    if arm == "noisy" and twin_passed is True and not obs.slot_extracted:
        return CheckOutcome("fail", "resolution_failure", False, ev)
    return CheckOutcome("fail", "routing_failure", False, ev)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_checker.py -v`
Expected: PASS (all — 3 typed + 6 classification).

- [ ] **Step 5: Commit**

```bash
git add autoeval/checker.py autoeval/tests/test_checker.py
git commit -m "feat(autoeval): failure-class assignment + A/B pairing (fabrication/resolution/routing/data_gap)"
```

---

### Task 8: Soft LLM-judge (Ollama) for fuzzy prose

**Files:**
- Create: `autoeval/judge.py`
- Test: `autoeval/tests/test_judge.py`

**Interfaces:**
- Consumes: reused `bot.services.ollama_client.OllamaClient`.
- Produces: `parse_verdict(raw) -> (verdict, confidence)`; `async judge(question, answer, ground_truth) -> (verdict, confidence)` (verdict ∈ correct|partial|wrong|error).

- [ ] **Step 1: Write the failing test** (pure parser — no Ollama)

```python
# autoeval/tests/test_judge.py
from autoeval.judge import parse_verdict

def test_parse_verdict_maps_words():
    assert parse_verdict("CORRECT")[0] == "correct"
    assert parse_verdict("the answer is PARTIAL, mostly")[0] == "partial"
    assert parse_verdict("WRONG")[0] == "wrong"
    assert parse_verdict("garbage output")[0] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_judge.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/judge.py
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))

_SYS = ("You compare a bot ANSWER to KNOWN FACTS about an item. Reply with ONE word: "
        "CORRECT (answer matches the facts), PARTIAL (partially right/incomplete), or "
        "WRONG (contradicts or unrelated). One word only.")

def parse_verdict(raw: str) -> tuple[str, float]:
    up = (raw or "").upper()
    for word, conf in (("CORRECT", 0.9), ("PARTIAL", 0.6), ("WRONG", 0.9)):
        if word in up:
            return word.lower(), conf
    return "error", 0.0

async def judge(question: str, answer: str, ground_truth: str) -> tuple[str, float]:
    """Soft signal ONLY. Never part of deterministic pass/fail."""
    from bot.services.ollama_client import OllamaClient
    client = OllamaClient()
    prompt = f"KNOWN FACTS:\n{ground_truth}\n\nQUESTION: {question}\nANSWER: {answer}\n\nVerdict:"
    try:
        raw = await client.generate(prompt=prompt, system=_SYS)
    except Exception:
        return "error", 0.0
    finally:
        try: await client.close()
        except Exception: pass
    return parse_verdict(raw)
```

> Confirm `OllamaClient().generate(prompt=..., system=...)` signature against `bot/services/ollama_client.py` in Step 1 of implementation; `scripts/eval_judge.py` uses the same call. If the kwarg names differ, match that file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_judge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add autoeval/judge.py autoeval/tests/test_judge.py
git commit -m "feat(autoeval): soft Ollama LLM-judge for fuzzy prose (separate signal)"
```

---

### Task 9: Codex client (subprocess + rate-limit detection)

**Files:**
- Create: `autoeval/codex_client.py` (adapted copy of teacher-eval `gather.py` codex functions)
- Create: `autoeval/answer_schema.json` (JSON schema for the 3-arm generation output — see Step 3)
- Test: `autoeval/tests/test_codex_client.py`

**Interfaces:**
- Produces: `RateLimitError`; `detect_rate_limit(rc, out, err) -> bool`; `parse_codex_output(jsonl) -> dict`; `extract_error_message(jsonl) -> Optional[str]`; `decide(out, err, rc) -> dict`; `run_codex(prompt, model=None, schema_path=...) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_codex_client.py
import json, pytest
from autoeval.codex_client import decide, detect_rate_limit, RateLimitError, extract_error_message

def _agent_msg(payload):
    return json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": json.dumps(payload)}})

def test_decide_parses_agent_message():
    out = _agent_msg({"questions": []})
    assert decide(out, "", 0) == {"questions": []}

def test_decide_raises_ratelimit_from_structured_error():
    err_event = json.dumps({"type": "error",
                            "message": "You've hit your usage limit. try again at 10:06 PM."})
    with pytest.raises(RateLimitError):
        decide(err_event, "", 1)

def test_extract_error_message_reads_turn_failed():
    line = json.dumps({"type": "turn.failed", "error": {"message": "usage limit; resets 3:00 AM"}})
    assert "resets" in extract_error_message(line)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_codex_client.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

Copy `RateLimitError`, `detect_rate_limit`, `parse_codex_output`, `extract_error_message`, `decide` **verbatim** from `.claude/worktrees/teacher-eval-phase1/teacher_eval/gather.py` (lines shown in the spec's reuse map) into `autoeval/codex_client.py`. Then add:

```python
# autoeval/codex_client.py  (after the copied functions)
import os, subprocess
SCHEMA = os.path.join(os.path.dirname(__file__), "answer_schema.json")
CODEX_TIMEOUT = 180

def run_codex(prompt: str, model: str | None = None, schema_path: str = SCHEMA) -> dict:
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
           "--output-schema", schema_path]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CODEX_TIMEOUT)
    return decide(result.stdout, result.stderr, result.returncode)
```

Create `autoeval/answer_schema.json` (constrains Codex to emit checkable specs):

```json
{
  "type": "object",
  "required": ["questions"],
  "properties": {
    "questions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["arm", "question", "expected"],
        "properties": {
          "arm": {"type": "string", "enum": ["answer", "noisy", "out_of_scope"]},
          "variant_type": {"type": "string", "enum": ["typo", "wording", "esl", "truncation"]},
          "twin_ref": {"type": "string"},
          "question": {"type": "string"},
          "expected": {
            "type": "object",
            "required": ["type"],
            "properties": {
              "type": {"type": "string",
                       "enum": ["contact", "count", "metric", "list", "abstain_or_clarify", "prose", "entity"]},
              "value": {"type": "string"},
              "must_contain_field": {"type": "string"},
              "members": {"type": "array", "items": {"type": "string"}},
              "missing_field": {"type": "string"},
              "skill_hint": {"type": "string"}
            }
          }
        }
      }
    }
  }
}
```

> Note: `run_codex` drops `-c tools.web_search=true` (the teacher-eval used web search; generation from a handed ground-truth item needs no web). Keep `-s read-only`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_codex_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/codex_client.py autoeval/answer_schema.json autoeval/tests/test_codex_client.py
git commit -m "feat(autoeval): codex client + rate-limit detection (adapted from teacher-eval)"
```

---

### Task 10: Question generator (3 arms) + expected-spec validation

**Files:**
- Create: `autoeval/generator.py`
- Test: `autoeval/tests/test_generator.py`

**Interfaces:**
- Consumes: `run_codex` (Task 9); `models.SourceItem/GeneratedQuestion/ExpectedSpec`.
- Produces: `build_prompt(item) -> str`; `parse_and_validate(raw_dict, item) -> list[GeneratedQuestion]` (drops any question lacking a checkable `expected` spec, logs the drop); `async generate(item, run_codex_fn=run_codex) -> list[GeneratedQuestion]`.

- [ ] **Step 1: Write the failing test** (inject a fake run_codex — no real Codex call)

```python
# autoeval/tests/test_generator.py
from autoeval.models import SourceItem
from autoeval.generator import parse_and_validate

def _item():
    return SourceItem(item_type="person", item_key="crawler/x", display_name="Jane Doe",
                      ground_truth={"email": "jdoe@njit.edu"}, has_fields=["email"],
                      missing_fields=["phone"])

def test_validate_keeps_checkable_and_drops_specless():
    raw = {"questions": [
        {"arm": "answer", "question": "email?",
         "expected": {"type": "contact", "value": "jdoe@njit.edu", "must_contain_field": "email"}},
        {"arm": "answer", "question": "bad", "expected": {"type": "contact"}},  # no value -> dropped
        {"arm": "out_of_scope", "question": "zzyzx?",
         "expected": {"type": "abstain_or_clarify"}},
    ]}
    qs = parse_and_validate(raw, _item())
    kinds = [(q.arm, q.expected.type) for q in qs]
    assert ("answer", "contact") in kinds
    assert ("out_of_scope", "abstain_or_clarify") in kinds
    assert len(qs) == 2  # the value-less contact question was dropped

def test_expected_item_key_is_forced_from_item():
    raw = {"questions": [{"arm": "answer", "question": "q",
            "expected": {"type": "contact", "value": "jdoe@njit.edu"}}]}
    qs = parse_and_validate(raw, _item())
    assert qs[0].expected.item_key == "crawler/x"  # never trusts Codex for the key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_generator.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/generator.py
from __future__ import annotations
import json, hashlib
from autoeval.models import SourceItem, GeneratedQuestion, ExpectedSpec

_ARM_INSTRUCTIONS = """You generate EVALUATION questions about ONE known item to stress-test a
university assistant. You are NOT answering; you produce questions plus a machine-checkable
`expected` spec derived ONLY from the KNOWN FACTS below.

Produce three arms:
- arm "answer": 3 questions whose answer IS in the known facts. expected.type is one of
  contact/count/metric/list, with expected.value (or members) taken verbatim from the facts.
- arm "noisy": for EACH answer question, 1-2 degraded variants (typo/wording/esl/truncation).
  Set variant_type and twin_ref (copy the exact answer-arm question text). SAME expected spec.
- arm "out_of_scope": 2 questions that CANNOT be answered from the facts (a fabricated person,
  an uncovered policy, a subjective 'who is best', OR a field the item LACKS). expected.type =
  abstain_or_clarify; if it targets a genuinely missing field, set expected.missing_field.

Return JSON matching the schema. Every question MUST carry a checkable expected spec."""

def build_prompt(item: SourceItem) -> str:
    facts = json.dumps({"item_type": item.item_type, "name": item.display_name,
                        "known_facts": item.ground_truth, "has_fields": item.has_fields,
                        "missing_fields": item.missing_fields}, indent=2)
    return f"{_ARM_INSTRUCTIONS}\n\nKNOWN FACTS:\n{facts}\n"

def _checkable(exp: dict) -> bool:
    t = exp.get("type")
    if t in ("contact", "count", "metric", "entity"):
        return bool(exp.get("value"))
    if t == "list":
        return bool(exp.get("members"))
    if t in ("abstain_or_clarify", "prose"):
        return True
    return False

def parse_and_validate(raw: dict, item: SourceItem) -> list[GeneratedQuestion]:
    ref = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]
    out: list[GeneratedQuestion] = []
    for q in raw.get("questions", []):
        exp = q.get("expected") or {}
        if not q.get("question") or not _checkable(exp):
            continue  # drop spec-less questions (loud: caller logs count dropped)
        spec = ExpectedSpec(
            type=exp["type"], item_key=item.item_key,          # key ALWAYS from the item, never Codex
            value=exp.get("value"), must_contain_field=exp.get("must_contain_field"),
            members=exp.get("members", []), skill_hint=exp.get("skill_hint"),
            missing_field=exp.get("missing_field"))
        out.append(GeneratedQuestion(
            arm=q["arm"], variant_type=q.get("variant_type"), twin_ref=q.get("twin_ref"),
            question_text=q["question"], expected=spec, item_type=item.item_type,
            item_key=item.item_key, codex_raw_ref=ref))
    return out

async def generate(item: SourceItem, run_codex_fn=None) -> list[GeneratedQuestion]:
    from autoeval.codex_client import run_codex
    fn = run_codex_fn or run_codex
    raw = fn(build_prompt(item))            # may raise RateLimitError -> caller pauses
    return parse_and_validate(raw, item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_generator.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/generator.py autoeval/tests/test_generator.py
git commit -m "feat(autoeval): 3-arm Codex generator + expected-spec validation (key forced from item)"
```

---

### Task 11: Resilience — status file + Codex-window auto-resume

**Files:**
- Create: `autoeval/resilience.py` (adapt `parse_reset_seconds` from teacher-eval `run_until_complete.py`)
- Test: `autoeval/tests/test_resilience.py`

**Interfaces:**
- Produces: `parse_reset_seconds(reason) -> Optional[float]`; `write_status(path, state, **fields)`; `read_status(path) -> dict`; `sleep_until_reset(reason, default=5400, buffer=300, sleep_fn=time.sleep) -> float` (returns seconds slept).

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_resilience.py
import json, tempfile, os
from autoeval.resilience import parse_reset_seconds, write_status, read_status, sleep_until_reset

def test_parse_bare_clock():
    assert parse_reset_seconds("try again at 10:06 PM") is not None

def test_parse_unparseable_returns_none():
    assert parse_reset_seconds("some unrelated error") is None

def test_status_roundtrip():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd)
    write_status(p, "paused", reason="usage limit", completed=5, total=10)
    st = read_status(p)
    assert st["state"] == "paused" and st["completed"] == 5

def test_sleep_until_reset_uses_default_when_unparseable():
    slept = []
    sleep_until_reset("no reset here", default=1234, buffer=0, sleep_fn=slept.append)
    assert slept == [1234]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_resilience.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

Copy `_MONTHS`, `_to_24h`, and `parse_reset_seconds` **verbatim** from `.claude/worktrees/teacher-eval-phase1/teacher_eval/run_until_complete.py` into `autoeval/resilience.py`, then add:

```python
# autoeval/resilience.py  (after the copied parse_reset_seconds)
import json, time
from datetime import datetime

def write_status(path: str, state: str, **fields) -> None:
    payload = {"state": state, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **fields}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    import os; os.replace(tmp, path)

def read_status(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def sleep_until_reset(reason: str, default: float = 5400, buffer: float = 300,
                      sleep_fn=time.sleep) -> float:
    wait = parse_reset_seconds(reason)
    total = (wait + buffer) if wait is not None else default
    sleep_fn(total)
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_resilience.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add autoeval/resilience.py autoeval/tests/test_resilience.py
git commit -m "feat(autoeval): status file + Codex-window reset parsing + auto-resume sleep"
```

---

### Task 12: Triage report

**Files:**
- Create: `autoeval/report.py`
- Test: `autoeval/tests/test_report.py`

**Interfaces:**
- Consumes: `Store.results_for_run` / `prev_run_at_commit` (Task 2).
- Produces: `build_report(rows, prev_rows=None) -> str` (pure — takes result dict rows, returns the markdown report).

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_report.py
from autoeval.report import build_report

def _row(**kw):
    base = dict(arm="answer", item_key="crawler/x", variant_type=None, result="pass",
                failure_class=None, data_gap=0, question_text="q", answer_text="a",
                evidence_json="{}", graded_soft=0)
    base.update(kw); return base

def test_report_counts_classes_separately_and_lists_fabrications():
    rows = [
        _row(result="fail", failure_class="fabrication", arm="out_of_scope",
             question_text="Zzyzx email?", answer_text="zzyzx@njit.edu"),
        _row(result="fail", failure_class="routing_failure"),
        _row(result="pass", data_gap=1),
        _row(result="pass"),
    ]
    rep = build_report(rows)
    assert "fabrication: 1" in rep.lower()
    assert "routing_failure: 1" in rep.lower()
    assert "Zzyzx email?" in rep            # fabrications listed in full
    assert "data_gap" in rep.lower()        # data gap reported separately
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_report.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/report.py
from __future__ import annotations
import json
from collections import Counter

def build_report(rows: list[dict], prev_rows: list[dict] | None = None) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r["result"] == "pass")
    fails = [r for r in rows if r["result"] == "fail"]
    classes = Counter(r["failure_class"] for r in fails if r["failure_class"])
    data_gaps = [r for r in rows if r.get("data_gap")]
    fabrications = [r for r in fails if r["failure_class"] == "fabrication"]

    L = []
    L.append("# Kavosh Auto-Eval — Triage Report\n")
    L.append(f"Total questions: {total}   Pass: {passed} ({100*passed/total:.1f}%)\n" if total else "No questions.\n")
    L.append("## Failure classes (separate; fabrication first)")
    L.append(f"- 🔴 fabrication: {classes.get('fabrication', 0)}")
    L.append(f"- resolution_failure: {classes.get('resolution_failure', 0)}")
    L.append(f"- routing_failure: {classes.get('routing_failure', 0)}")
    L.append(f"- data_gap (data problem, NOT a Kavosh bug): {len(data_gaps)}\n")

    L.append("## 🔴 Fabrications (full list — zero tolerance)")
    if not fabrications:
        L.append("- none\n")
    for r in fabrications:
        L.append(f"- [{r['item_key']}] Q: {r['question_text']}\n    A: {r['answer_text'][:200]}")
    L.append("")

    # Top failing items
    item_fails = Counter(r["item_key"] for r in fails)
    L.append("## Top failing items")
    for key, c in item_fails.most_common(15):
        L.append(f"- {key}: {c} failures")
    L.append("")

    # Resolution failures by variant_type
    res = [r for r in fails if r["failure_class"] == "resolution_failure"]
    vt = Counter(r.get("variant_type") for r in res)
    L.append("## Resolution failures by variant_type (WS2 tuning surface)")
    for v, c in vt.most_common():
        L.append(f"- {v}: {c}")
    L.append("")

    # Data-gap report (separate)
    L.append("## Data-gap report (route to crawler backlog — NOT routing bugs)")
    dg = Counter(r["item_key"] for r in data_gaps)
    for key, c in dg.most_common(30):
        L.append(f"- {key}: {c} missing-field questions correctly abstained")
    L.append("")

    # Regression delta
    if prev_rows is not None and prev_rows:
        p_total = len(prev_rows); p_pass = sum(1 for r in prev_rows if r["result"] == "pass")
        p_fab = sum(1 for r in prev_rows if r["failure_class"] == "fabrication")
        cur_rate = 100*passed/total if total else 0
        prev_rate = 100*p_pass/p_total if p_total else 0
        L.append("## Regression delta (vs previous run at same commit)")
        L.append(f"- pass rate: {prev_rate:.1f}% → {cur_rate:.1f}%  (Δ {cur_rate-prev_rate:+.1f})")
        L.append(f"- fabrications: {p_fab} → {len(fabrications)}  (Δ {len(fabrications)-p_fab:+d})")
    return "\n".join(L)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add autoeval/report.py autoeval/tests/test_report.py
git commit -m "feat(autoeval): triage report (classes separate, fabrications full, data-gap apart)"
```

---

### Task 13: Live visibility (`tail` / `status`) + launcher script

**Files:**
- Create: `autoeval/live.py`
- Create: `scripts/autoeval.sh`
- Test: `autoeval/tests/test_report.py` (add one live-format test) — or a small `test_live.py`

**Interfaces:**
- Consumes: `Store` (Task 2), `read_status` (Task 11).
- Produces: `format_status(status, store, run_id) -> str`; `recent_rows(store, run_id, n) -> list[dict]`; `format_tail(rows) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# autoeval/tests/test_live.py
from autoeval.live import format_tail, format_status

def test_format_tail_shows_arm_and_verdict():
    rows = [{"arm": "out_of_scope", "question_text": "Zzyzx?", "answer_text": "no idea",
             "result": "pass", "failure_class": None}]
    s = format_tail(rows)
    assert "out_of_scope" in s and "Zzyzx?" in s and "pass" in s

def test_format_status_shows_state():
    s = format_status({"state": "paused", "reason": "try again at 10:06 PM"},
                      running_counts={"total": 10, "pass": 7, "fabrication": 1})
    assert "paused" in s and "10:06 PM" in s and "fabrication" in s.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest autoeval/tests/test_live.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# autoeval/live.py
from __future__ import annotations

def format_tail(rows: list[dict]) -> str:
    out = []
    for r in rows:
        verdict = r["result"] if not r.get("failure_class") else f"{r['result']}/{r['failure_class']}"
        out.append(f"[{r['arm']:12}] {verdict:22} Q: {r['question_text'][:60]}\n"
                   f"                              A: {(r.get('answer_text') or '')[:80]}")
    return "\n".join(out) if out else "(no rows yet)"

def format_status(status: dict, running_counts: dict | None = None) -> str:
    state = status.get("state", "unknown")
    line = f"STATE: {state}"
    if state == "paused":
        line += f"   (resume: {status.get('reason', '?')})"
    if status.get("updated_at"):
        line += f"   @ {status['updated_at']}"
    c = running_counts or {}
    counts = (f"\nprogress: {c.get('pass',0)}/{c.get('total',0)} pass   "
              f"fabrication: {c.get('fabrication',0)}") if c else ""
    return line + counts
```

Create `scripts/autoeval.sh`:

```bash
#!/usr/bin/env bash
# Kavosh auto-eval harness launcher. Pins the required env BEFORE python imports bot.config.
set -euo pipefail
cd /home/md724/gsa-gateway
export ROUTER_V21=1
export ROUTER_V21_SHADOW=0
export LIVE_ENABLED=0
export ROUTER_V21_SLOT_RECOVERY=0
export PYTHONPATH="$PWD"
PY=".venv/bin/python"
cmd="${1:-run}"; shift || true
case "$cmd" in
  run)    exec "$PY" -m autoeval.harness "$@" ;;
  smoke)  exec "$PY" -m autoeval.harness --smoke --items "${1:-50}" ;;
  status) exec "$PY" -m autoeval.live_cli status ;;
  tail)   exec "$PY" -m autoeval.live_cli tail "${1:-20}" ;;
  *) echo "usage: autoeval.sh {run|smoke [N]|status|tail [N]}"; exit 2 ;;
esac
```

Create `autoeval/live_cli.py` (thin CLI wrapper):

```python
# autoeval/live_cli.py
import sys
from autoeval.config import load_config
from autoeval.store import Store
from autoeval.resilience import read_status
from autoeval.live import format_status, format_tail

def _latest_run(store):
    row = store.conn.execute("SELECT MAX(run_id) AS r FROM runs").fetchone()
    return row["r"] if row and row["r"] else None

def main():
    cfg = load_config(); store = Store(cfg.autoeval_db); store.init_schema()
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    run_id = _latest_run(store)
    rows = store.results_for_run(run_id) if run_id else []
    if action == "tail":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(format_tail([r for r in rows if r.get("result")][-n:]))
    else:
        counts = {"total": len([r for r in rows if r.get("result")]),
                  "pass": sum(1 for r in rows if r.get("result") == "pass"),
                  "fabrication": sum(1 for r in rows if r.get("failure_class") == "fabrication")}
        print(format_status(read_status(cfg.status_file), counts))

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest autoeval/tests/test_live.py -v`
Expected: PASS. Then `chmod +x scripts/autoeval.sh`.

- [ ] **Step 5: Commit**

```bash
git add autoeval/live.py autoeval/live_cli.py scripts/autoeval.sh autoeval/tests/test_live.py
git commit -m "feat(autoeval): live tail/status CLI + launcher with pinned env"
```

---

### Task 14: Harness main loop (one run window: sample → generate → run → check → store → report)

**Files:**
- Create: `autoeval/harness.py`
- Test: covered by the Task 15 end-to-end smoke (the loop wires already-tested units; no new unit logic worth isolating).

**Interfaces:**
- Consumes: every prior module.
- Produces: `async run_window(cfg, n_items, smoke=False)`; CLI `python -m autoeval.harness [--smoke] [--items N]`.

- [ ] **Step 1: Write the implementation**

```python
# autoeval/harness.py
from __future__ import annotations
import argparse, asyncio, json, subprocess, sys
from pathlib import Path
from autoeval.config import load_config, assert_env
from autoeval.snapshot import make_snapshot, ro_connect
from autoeval.store import Store
from autoeval.sampler import sample_items
from autoeval.generator import generate
from autoeval.runner import KavoshRunner
from autoeval.checker import classify, check_typed
from autoeval.judge import judge
from autoeval.report import build_report
from autoeval.resilience import write_status, sleep_until_reset
from autoeval.codex_client import RateLimitError

def _kavosh_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"

async def run_window(cfg, n_items: int, smoke: bool = False):
    assert_env()
    store = Store(cfg.autoeval_db); store.init_schema()
    snap, snap_hash = make_snapshot(cfg.prod_db, cfg.snapshot_dir)
    gt_conn = ro_connect(snap)
    run_id = store.create_run(db_snapshot_hash=snap_hash, config_json=json.dumps(cfg.__dict__, default=str),
                              codex_model=cfg.codex_model or "default", kavosh_commit=_kavosh_commit(),
                              live_enabled=cfg.live_enabled)
    write_status(cfg.status_file, "running", run_id=run_id, completed=0, total=0)

    prefer = store.least_tested_keys(limit=n_items * 3)
    items = sample_items(gt_conn, cfg.sampler_mix, n_items, prefer_keys=prefer, seed=None if not smoke else 1)

    runner = KavoshRunner(cfg); await runner.build(snap)
    # arm-A pass tracking for A/B pairing, keyed by (item_key, twin question text)
    twin_pass: dict[tuple[str, str], bool] = {}
    completed = 0
    try:
        for item in items:
            try:
                questions = await generate(item)
            except RateLimitError as e:
                write_status(cfg.status_file, "paused", run_id=run_id, reason=str(e),
                             completed=completed, total=len(items))
                sleep_until_reset(str(e))               # auto-resume after Codex window
                write_status(cfg.status_file, "running", run_id=run_id, completed=completed, total=len(items))
                questions = await generate(item)         # retry once window reopens
            # order: answer arm first so twins are known before noisy arm
            questions.sort(key=lambda q: {"answer": 0, "noisy": 1, "out_of_scope": 2}[q.arm])
            for q in questions:
                obs = await runner.observe(q.question_text)
                twin_passed = None
                if q.arm == "noisy" and q.twin_ref:
                    twin_passed = twin_pass.get((item.item_key, q.twin_ref))
                outcome = classify(q.expected, obs, q.arm, item.missing_fields, twin_passed)
                if outcome.graded_soft:
                    v, c = await judge(q.question_text, obs.answer_text, json.dumps(item.ground_truth))
                    outcome.llm_judge_verdict, outcome.llm_judge_confidence = v, c
                if q.arm == "answer":
                    twin_pass[(item.item_key, q.question_text)] = (outcome.result == "pass")
                q_id = store.insert_question(
                    run_id, item_type=q.item_type, item_key=q.item_key, arm=q.arm,
                    variant_type=q.variant_type, twin_ref=q.twin_ref, question_text=q.question_text,
                    expected_json=json.dumps(q.expected.__dict__), codex_raw_ref=q.codex_raw_ref)
                store.insert_result(
                    q_id, answer_text=obs.answer_text,
                    metadata_json=json.dumps({"source_note": obs.source_note, "is_live": obs.is_live}),
                    result=outcome.result, failure_class=outcome.failure_class, data_gap=outcome.data_gap,
                    evidence_json=json.dumps(outcome.evidence), latency_ms=obs.latency_ms,
                    resolved_entity_id=obs.resolved_key, family=obs.family, skill=obs.skill,
                    used_ai=obs.used_ai, graded_soft=outcome.graded_soft,
                    llm_judge_verdict=outcome.llm_judge_verdict, llm_judge_confidence=outcome.llm_judge_confidence)
            store.bump_coverage(item.item_key)
            completed += 1
            write_status(cfg.status_file, "running", run_id=run_id, completed=completed, total=len(items))
    finally:
        await runner.close()

    rows = store.results_for_run(run_id)
    prev = store.prev_run_at_commit(_kavosh_commit(), run_id)
    prev_rows = store.results_for_run(prev) if prev else None
    report = build_report(rows, prev_rows)
    out_path = Path(cfg.repo_root) / "autoeval" / f"report_run_{run_id}.md"
    out_path.write_text(report, encoding="utf-8")
    write_status(cfg.status_file, "done", run_id=run_id, completed=completed, total=len(items),
                 report=str(out_path))
    print(f"run {run_id} complete → {out_path}")
    return run_id

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--items", type=int, default=50)
    args = ap.parse_args()
    cfg = load_config()
    asyncio.run(run_window(cfg, args.items, smoke=args.smoke))

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit** (no unit test here — exercised end-to-end in Task 15)

```bash
git add autoeval/harness.py
git commit -m "feat(autoeval): run-window orchestration (sample→generate→run→check→store→report)"
```

---

### Task 15: End-to-end smoke + checker calibration (the trust gate)

**Files:**
- Create: `autoeval/tests/test_calibration.py`
- No production code — this task proves the harness before the 24/7 loop.

**Interfaces:**
- Consumes: `classify` (Task 7) with hand-authored observations.

- [ ] **Step 1: Write the calibration test (hand-labeled cases → expected class)**

```python
# autoeval/tests/test_calibration.py
"""Spec §9 trust gate: feed the checker hand-labeled answers, confirm each failure_class.
This is the instrument calibration — it must pass before enabling the long run."""
from autoeval.models import ExpectedSpec, KavoshObservation
from autoeval.checker import classify

def _o(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

CASES = [
    # (expected, obs, arm, missing_fields, twin_passed, want_result, want_class, want_datagap)
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("Jane's email is **jdoe@njit.edu**."), "answer", [], None, "pass", None, False),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("Jane's email is bad@njit.edu."), "answer", [], None, "fail", "fabrication", False),
    (ExpectedSpec("abstain_or_clarify", "crawler/zzyzx"),
     _o("Prof Zzyzx's email is z@njit.edu"), "out_of_scope", [], None, "fail", "fabrication", False),
    (ExpectedSpec("abstain_or_clarify", "crawler/x", missing_field="phone"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "out_of_scope", ["phone"], None, "pass", None, True),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "noisy", [], True, "fail", "resolution_failure", False),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "answer", [], None, "fail", "routing_failure", False),
]

def test_calibration_matrix():
    for exp, obs, arm, miss, twin, wr, wc, wg in CASES:
        out = classify(exp, obs, arm, miss, twin)
        assert (out.result, out.failure_class, out.data_gap) == (wr, wc, wg), (exp.type, arm, out)
```

- [ ] **Step 2: Run the calibration test**

Run: `.venv/bin/python -m pytest autoeval/tests/test_calibration.py -v`
Expected: PASS (this is spec §9 — the four discriminations hold on hand-labeled data).

- [ ] **Step 3: Run the full unit suite**

Run: `.venv/bin/python -m pytest autoeval/tests/ -v`
Expected: ALL PASS.

- [ ] **Step 4: Isolation proof (production untouched)**

Run:
```bash
cd /home/md724/gsa-gateway
before=$(sqlite3 gsa_gateway.db "SELECT COUNT(*) FROM questions")
git stash list >/dev/null   # (no-op guard)
# run a tiny smoke against the snapshot (needs Ollama up; use --items 3 to keep it fast)
bash scripts/autoeval.sh smoke 3 || true
after=$(sqlite3 gsa_gateway.db "SELECT COUNT(*) FROM questions")
echo "prod questions before=$before after=$after"
test -f logs/router_v21_shadow.jsonl && echo "SHADOW LOG PRESENT (investigate)" || echo "no shadow log (good)"
```
Expected: `before == after` (zero production analytics rows added), and no new shadow-log rows. If either fails, STOP — isolation is broken.

- [ ] **Step 5: Eyeball the smoke report**

Run: `cat autoeval/report_run_*.md | head -60`
Expected: the four failure-class counts are present and separate, fabrications (if any) are listed in full, and data-gap is its own section. Confirm a few verdicts match your own judgment of the printed Q/A pairs.

- [ ] **Step 6: Commit**

```bash
git add autoeval/tests/test_calibration.py
git commit -m "test(autoeval): §9 calibration matrix + isolation proof (trust gate before 24/7)"
```

- [ ] **Step 7: Long-run launch (only after Steps 1-6 pass)**

```bash
cd /home/md724/gsa-gateway
nohup bash scripts/autoeval.sh run --items 200 > autoeval/harness.log 2>&1 &
# peek anytime:
bash scripts/autoeval.sh status
bash scripts/autoeval.sh tail 20
```

---

## Self-Review

**Spec coverage:**
- Item sampler + ground-truth + `missing_fields` → Task 4 ✓ (area/chunk extractors explicitly DEFERRED and marked loud — see Task 4b note).
- Results DB + trivial plumbing path → Tasks 2, 14 ✓.
- Deterministic checker typed cases → Task 6 ✓.
- Codex generator 3 arms → Tasks 9, 10 ✓ (Arm A/C/B all covered; key forced from item).
- Failure-class + A/B pairing → Task 7 ✓.
- Triage report → Task 12 ✓.
- Long-run wrapper + coverage sweep + GPU-polite + auto-resume + graceful resume → Tasks 11, 13, 14 ✓ (concurrency defaults to 1 = GPU-polite; auto-resume in `run_window`).
- Isolation / not-user-questions → Task 5 (snapshot repoint, combined mode), Task 15 Step 4 (proof) ✓.
- Required env fail-fast → Task 1 + Task 13 launcher ✓.
- Live visibility → Task 13 ✓.
- Soft LLM-judge separate → Task 8 + Task 14 wiring ✓.
- §9 calibration gate → Task 15 ✓.

**Known deferrals (loud, not silent):** area/chunk sampler extractors (Task 4b); concurrency >1 (default 1 is intentional GPU-politeness — raising it is a config change, not new code); `list` precision is coarse (recall is the graded signal, noted in Task 6).

**Type consistency:** `SourceItem`/`ExpectedSpec`/`GeneratedQuestion`/`KavoshObservation`/`CheckOutcome` field names are fixed in `models.py` (Task 4) and used identically in Tasks 5-14. `classify(expected, obs, arm, missing_fields, twin_passed)` signature matches its call site in `harness.py`. `Store` method names match their callers.

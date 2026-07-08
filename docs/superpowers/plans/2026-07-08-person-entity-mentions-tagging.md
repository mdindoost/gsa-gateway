# Person Entity-Mentions Tagging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface owned KB prose (awards + curated bio/news) on the bot's person answer by tagging each KB item to the person(s) it is about, once, offline — then appending it verbatim to the entity card.

**Architecture:** An offline, gated tagger writes a many-to-many `entity_mentions` table (KB item ↔ Person node) using a deterministic gate. Serving computes an addendum payload inside the existing worker threads and appends it verbatim (length-budgeted) in the shared `structured_answer` layer, so both live answer paths + the resume path inherit it. RAG is untouched.

**Tech Stack:** Python 3.11, SQLite (STRICT tables), pytest. No new deps.

**Binding source:** `docs/superpowers/specs/2026-07-07-person-entity-mentions-tagging-design.md` — **§15 REVISION v2 is authoritative (R1–R12)**; where the body and §15 differ, follow §15.

## Global Constraints

- **Gated live writes:** any script writing the live DB defaults to dry-run, requires `--commit`, and calls `scripts/_area_tag_migrate.hardened_backup(db_path, label, keep=10)` first. Writes via a self-owned short-lived writable conn — NEVER a passed graph-write conn.
- **Verbatim / never-withheld:** appended prose is emitted verbatim; NEVER truncated mid-text; NEVER handed to the LLM. A prose item is shown WHOLE or omitted (never partial). Prose is never "stubbed."
- **Anti-fabrication:** no LLM sees merged KG+KB context. Addendum = data appended after compose, like `deterministic_suffix`.
- **LLM-agnostic; no arbitrary caps:** the gate is deterministic (no model) by default; caps (`EM_ROSTER_N`, `AWARD_CAP`) are config tunables.
- **Never insert `search_text`** (generated column) — untouched here.
- **Table location:** `entity_mentions` lives in the KNOWLEDGE schema (`_KNOWLEDGE_TABLE_DDL` + `_KNOWLEDGE_INDEXES`), NOT OPS (R2).
- **Stable key (R3):** serve/PK by `stable_key = COALESCE(natural_key, 'id:'||item_id)` joined to the live `is_active=1` row.
- **Flag defaults at merge (R8):** `PERSON_ADDENDUM_ENABLED=ON` (awards), `PERSON_MENTIONS_ENABLED=OFF` (flip after audit).
- **Person name storage:** `nodes.name` is `"Last, First …"`; normalize with `v2.core.retrieval.entity.normalize_person_name`.

---

### Task 1: `entity_mentions` table in the KNOWLEDGE schema

**Files:**
- Modify: `v2/core/database/schema.py` (append to `_KNOWLEDGE_TABLE_DDL` ~:576 and `_KNOWLEDGE_INDEXES` ~:475)
- Test: `v2/tests/test_entity_mentions_schema.py`

**Interfaces:**
- Produces: table `entity_mentions(stable_key TEXT, node_key TEXT, item_id INTEGER, node_id INTEGER, match_basis TEXT, confidence REAL, created_by TEXT, created_at TEXT)`, PK `(stable_key, node_key)`; indexes `idx_em_node(node_key)`, `idx_em_stable(stable_key)`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_entity_mentions_schema.py
import sqlite3, tempfile, os
from v2.core.database.schema import create_knowledge_schema

def test_entity_mentions_in_knowledge_schema():
    d = tempfile.mkdtemp(); p = os.path.join(d, "k.db")
    conn = create_knowledge_schema(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_mentions)")}
    assert {"stable_key","node_key","item_id","node_id","match_basis","confidence","created_by","created_at"} <= cols
    idx = {r[0] for r in conn.execute("PRAGMA index_list(entity_mentions)")}
    assert any("em_node" in i for i in idx)
    # created_at default present (insert without it succeeds)
    conn.execute("INSERT INTO entity_mentions(stable_key,node_key,item_id,node_id,match_basis) VALUES('id:64','k',64,1,'title')")
    conn.commit()
    row = conn.execute("SELECT created_at,created_by FROM entity_mentions").fetchone()
    assert row[0] and row[1] == "entity_mentions_tagger"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_entity_mentions_schema.py -q`
Expected: FAIL (no such table: entity_mentions)

- [ ] **Step 3: Add the DDL**

Append to `_KNOWLEDGE_TABLE_DDL` (the list ~:576):

```python
    """
    CREATE TABLE IF NOT EXISTS entity_mentions (
        stable_key   TEXT    NOT NULL,   -- COALESCE(natural_key, 'id:'||item_id)
        node_key     TEXT    NOT NULL,   -- nodes.key (entity_id)
        item_id      INTEGER NOT NULL,   -- knowledge_items.id (audit/convenience)
        node_id      INTEGER NOT NULL,   -- nodes.id (audit)
        match_basis  TEXT    NOT NULL,   -- 'title' | 'both_names' | 'llm_verified'
        confidence   REAL,
        created_by   TEXT    NOT NULL DEFAULT 'entity_mentions_tagger',
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (stable_key, node_key)
    ) STRICT;
    """,
```

Append to `_KNOWLEDGE_INDEXES` (~:475):

```python
    "CREATE INDEX IF NOT EXISTS idx_em_node ON entity_mentions(node_key);",
    "CREATE INDEX IF NOT EXISTS idx_em_stable ON entity_mentions(stable_key);",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_entity_mentions_schema.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/schema.py v2/tests/test_entity_mentions_schema.py
git commit -m "feat(entity-mentions): add entity_mentions table to knowledge schema"
```

---

### Task 2: The resolution gate (deterministic, per-item)

**Files:**
- Create: `v2/core/ingestion/entity_mentions.py`
- Test: `v2/tests/test_entity_mentions_gate.py`

**Interfaces:**
- Produces:
  - `PersonName = namedtuple("PersonName", "node_id node_key last first")`
  - `load_person_index(conn) -> list[PersonName]` — all active Person nodes, normalized.
  - `resolve_item(title: str, content: str, people: list[PersonName], roster_n: int = 5) -> list[tuple[PersonName, str, float]]` — returns `(person, basis, confidence)` for each accepted person; `[]` for roster/none. `basis ∈ {"title","both_names"}`.
- Consumes: `entity.normalize_person_name`.

**Gate rules (spec §5.2 / R10):**
1. TITLE fast-path: person full name (both tokens, whole-word) in `title` → accept, basis `title`, conf 1.0.
2. BODY: both name tokens whole-word in `content` → candidate.
   - anti-roster: if target appears exactly once AND ≥ `roster_n` OTHER distinct people appear whole-word → reject.
   - namesake: if >1 active person shares the full (last,first) name AND no shared corroborating token → abstain (skip).
   - else accept, basis `both_names`, conf 0.7.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_entity_mentions_gate.py
from v2.core.ingestion.entity_mentions import resolve_item, PersonName

ORIA = PersonName(15, "people.njit.edu/profile/oria", "Oria", "Vincent")
SATOH = PersonName(20, "x/satoh", "Satoh", "Shinichi")
def many(n): return [PersonName(100+i, f"x/p{i}", f"Last{i}", f"First{i}") for i in range(n)]

def test_title_fastpath_accepts_bio():
    out = resolve_item("Who is Prof. Vincent Oria?", "Vincent Oria is a Professor ...", [ORIA])
    assert out and out[0][0].node_key == ORIA.node_key and out[0][1] == "title"

def test_memorial_substring_rejected():
    # 'Oria' inside 'Memorial'; first name absent -> not both-names -> no match
    out = resolve_item("Award", "2010 Franklin V. Taylor Memorial Award", [ORIA])
    assert out == []

def test_both_names_body_accepts_news():
    out = resolve_item("MMI 2026", "The organizing committee was Vincent Oria (NJIT) and Shinichi Satoh.", [ORIA, SATOH])
    keys = {p.node_key for p,_,_ in out}
    assert ORIA.node_key in keys and SATOH.node_key in keys

def test_roster_page_rejected():
    # target appears once + many other known people -> roster
    people = [ORIA] + many(6)
    body = "Professor Oria, Vincent Professor " + " ".join(f"First{i} Last{i}" for i in range(6))
    out = resolve_item("Ph.D. Computer Science", body, people)
    assert all(p.node_key != ORIA.node_key for p,_,_ in out)

def test_multiperson_news_accepted_not_roster():
    # a genuine news item names a few collaborators but is ABOUT the subject (appears twice) -> accept (R10)
    body = ("From Byblos to Newark: Fadi Deek’s memoir. Fadi Deek reflects with colleagues "
            "First0 Last0 and First1 Last1.")
    deek = PersonName(9, "x/deek", "Deek", "Fadi")
    out = resolve_item("News", body, [deek] + many(2))
    assert any(p.node_key == "x/deek" for p,_,_ in out)
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest v2/tests/test_entity_mentions_gate.py -q`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement the gate**

```python
# v2/core/ingestion/entity_mentions.py
"""Offline entity-mentions tagger — resolve which Person node(s) a KB item is ABOUT.
Deterministic gate (no LLM by default). See spec 2026-07-07 §5/§15 R10."""
from __future__ import annotations
import re
from collections import namedtuple
from v2.core.retrieval.entity import normalize_person_name

PersonName = namedtuple("PersonName", "node_id node_key last first")

def _first_last(normalized: str) -> tuple[str, str]:
    parts = normalized.split()
    return (parts[-1], parts[0]) if len(parts) >= 2 else (normalized, "")

def load_person_index(conn) -> list[PersonName]:
    out = []
    for nid, key, raw in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        last, first = _first_last(normalize_person_name(raw))
        if last:
            out.append(PersonName(nid, key, last, first))
    return out

def _whole(word: str, text: str) -> list[int]:
    if not word:
        return []
    return [m.start() for m in re.finditer(r"\b" + re.escape(word) + r"\b", text, re.I)]

def _both_names_hits(p: PersonName, text: str) -> int:
    """count of target occurrences requiring BOTH first & last whole-word present at all."""
    if not (p.first and _whole(p.first, text)):
        return 0
    return len(_whole(p.last, text))

def resolve_item(title: str, content: str, people: list[PersonName], roster_n: int = 5):
    title = title or ""; content = content or ""
    # namesake detection: full (last,first) appearing on >1 node
    from collections import Counter
    fullkeys = Counter((p.last.lower(), p.first.lower()) for p in people)
    accepted = []
    # who is present at all in the body (both-names) -> for anti-roster other-count
    body_present = [p for p in people if _both_names_hits(p, content) > 0]
    for p in people:
        # TITLE fast-path
        if p.first and _whole(p.last, title) and _whole(p.first, title):
            accepted.append((p, "title", 1.0)); continue
        hits = _both_names_hits(p, content)
        if hits == 0:
            continue
        # namesake abstain (no corroboration model in phase 1 -> abstain)
        if fullkeys[(p.last.lower(), p.first.lower())] > 1:
            continue
        # anti-roster: target once + many OTHER known people present
        others = sum(1 for q in body_present if q.node_key != p.node_key)
        if hits == 1 and others >= roster_n:
            continue
        accepted.append((p, "both_names", 0.7))
    return accepted
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest v2/tests/test_entity_mentions_gate.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/entity_mentions.py v2/tests/test_entity_mentions_gate.py
git commit -m "feat(entity-mentions): deterministic resolution gate (title/both-names/anti-roster/namesake)"
```

---

### Task 3: Build pass + audit CSV

**Files:**
- Modify: `v2/core/ingestion/entity_mentions.py`
- Test: `v2/tests/test_entity_mentions_build.py`

**Interfaces:**
- Produces:
  - `IN_SCOPE_TYPES = ("faq", "news", "event_info")`  (R5/R6 — no award/about)
  - `build_mentions(conn, *, roster_n=5, audit_writer=None) -> list[dict]` — resolves all in-scope active items, RETURNS the accepted rows as dicts `{stable_key,node_key,item_id,node_id,match_basis,confidence,title,person}` (does NOT write); caller writes. If `audit_writer` (a csv.writer) given, writes one audit row per accepted pair.
  - `stable_key_of(item_id, natural_key) -> str` = `natural_key or f"id:{item_id}"`.
  - `write_mentions(conn, rows)` — DELETE FROM entity_mentions WHERE created_by='entity_mentions_tagger'; INSERT the rows. (Full rebuild in own scope.)

- [ ] **Step 1: Write the failing test** (fixture DB with an Oria node + id=64-style bio + a roster page)

```python
# v2/tests/test_entity_mentions_build.py
import sqlite3
from v2.core.database.schema import create_knowledge_schema
from v2.core.ingestion.entity_mentions import build_mentions, write_mentions

def _fixture():
    conn = create_knowledge_schema(":memory:")
    conn.execute("INSERT INTO nodes(type,key,name,is_active) VALUES('Person','k/oria','Oria, Vincent',1)")
    conn.execute("INSERT INTO nodes(type,key,name,is_active) VALUES('Person','k/satoh','Satoh, Shinichi',1)")
    # curated bio (no natural_key) -> stable_key must fall back to id:
    conn.execute("INSERT INTO knowledge_items(type,title,content,is_active,created_by) "
                 "VALUES('faq','Who is Prof. Vincent Oria?','Vincent Oria is a Professor and Chair.',1,'migration')")
    # news naming both
    conn.execute("INSERT INTO knowledge_items(type,title,content,is_active,created_by,metadata) "
                 "VALUES('news','MMI','Committee: Vincent Oria and Shinichi Satoh.',1,'college_crawl','{\"natural_key\":\"nk-mmi\"}')")
    conn.commit(); return conn

def test_build_tags_bio_and_news():
    conn = _fixture()
    rows = build_mentions(conn)
    by_person = {(r["node_key"], r["title"][:5]) for r in rows}
    assert ("k/oria","Who i") in by_person       # bio -> Oria (title fast-path)
    assert ("k/oria","MMI") in by_person and ("k/satoh","MMI") in by_person
    # stable_key: bio has no natural_key -> id: prefix; news -> natural_key
    bio = next(r for r in rows if r["title"].startswith("Who"))
    assert bio["stable_key"].startswith("id:")
    news = next(r for r in rows if r["title"] == "MMI")
    assert news["stable_key"] == "nk-mmi"

def test_write_then_serving_join():
    conn = _fixture(); write_mentions(conn, build_mentions(conn))
    n = conn.execute("SELECT count(*) FROM entity_mentions WHERE node_key='k/oria'").fetchone()[0]
    assert n == 2
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest v2/tests/test_entity_mentions_build.py -q` → FAIL

- [ ] **Step 3: Implement `build_mentions` / `write_mentions` / `stable_key_of`**

```python
import json
IN_SCOPE_TYPES = ("faq", "news", "event_info")

def stable_key_of(item_id: int, natural_key) -> str:
    return natural_key or f"id:{item_id}"

def build_mentions(conn, *, roster_n: int = 5, audit_writer=None) -> list[dict]:
    people = load_person_index(conn)
    node_id_by_key = {p.node_key: p.node_id for p in people}
    q = ("SELECT id, title, content, json_extract(metadata,'$.natural_key') "
         "FROM knowledge_items WHERE is_active=1 AND type IN (%s)"
         % ",".join("?" * len(IN_SCOPE_TYPES)))
    rows = []
    for item_id, title, content, nkey in conn.execute(q, IN_SCOPE_TYPES):
        for person, basis, conf in resolve_item(title or "", content or "", people, roster_n):
            sk = stable_key_of(item_id, nkey)
            rows.append({"stable_key": sk, "node_key": person.node_key,
                         "item_id": item_id, "node_id": person.node_id,
                         "match_basis": basis, "confidence": conf,
                         "title": title or "", "person": f"{person.first} {person.last}"})
            if audit_writer:
                audit_writer.writerow([item_id, title, person.first + " " + person.last, basis, conf])
    return rows

def write_mentions(conn, rows: list[dict]) -> int:
    conn.execute("DELETE FROM entity_mentions WHERE created_by='entity_mentions_tagger'")
    conn.executemany(
        "INSERT OR REPLACE INTO entity_mentions"
        "(stable_key,node_key,item_id,node_id,match_basis,confidence) VALUES(?,?,?,?,?,?)",
        [(r["stable_key"], r["node_key"], r["item_id"], r["node_id"],
          r["match_basis"], r["confidence"]) for r in rows])
    return len(rows)
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest v2/tests/test_entity_mentions_build.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/entity_mentions.py v2/tests/test_entity_mentions_build.py
git commit -m "feat(entity-mentions): build pass + audit + write (full rebuild in own scope)"
```

---

### Task 4: Gated runner script

**Files:**
- Create: `scripts/tag_entity_mentions.py`
- Test: `v2/tests/test_tag_entity_mentions_cli.py`

**Interfaces:**
- Consumes: `build_mentions`, `write_mentions`, `hardened_backup`.
- Produces: CLI `python scripts/tag_entity_mentions.py [--db PATH] [--commit] [--audit out.csv] [--roster-n N]`. Dry-run default: prints count + writes audit, does NOT touch the DB. `--commit`: hardened_backup then write on a self-owned writable conn.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_tag_entity_mentions_cli.py
import subprocess, sqlite3, sys
from v2.core.database.schema import create_knowledge_schema

def _seed(p):
    c = create_knowledge_schema(p)
    c.execute("INSERT INTO nodes(type,key,name,is_active) VALUES('Person','k/oria','Oria, Vincent',1)")
    c.execute("INSERT INTO knowledge_items(type,title,content,is_active,created_by) "
              "VALUES('faq','Who is Prof. Vincent Oria?','Vincent Oria is Chair.',1,'migration')")
    c.commit(); c.close()

def test_dryrun_writes_nothing(tmp_path):
    p = str(tmp_path/"k.db"); _seed(p)
    r = subprocess.run([sys.executable,"scripts/tag_entity_mentions.py","--db",p],
                       capture_output=True, text=True)
    assert r.returncode == 0
    n = sqlite3.connect(p).execute("SELECT count(*) FROM entity_mentions").fetchone()[0]
    assert n == 0                                    # dry-run

def test_commit_writes(tmp_path):
    p = str(tmp_path/"k.db"); _seed(p)
    r = subprocess.run([sys.executable,"scripts/tag_entity_mentions.py","--db",p,"--commit"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    n = sqlite3.connect(p).execute("SELECT count(*) FROM entity_mentions WHERE node_key='k/oria'").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run to verify fail** — FAIL (no script)

- [ ] **Step 3: Implement the runner**

```python
# scripts/tag_entity_mentions.py
"""Gated entity-mentions tagger runner. Dry-run default; --commit writes (hardened_backup first)."""
import argparse, csv, sqlite3, sys
from v2.core.ingestion.entity_mentions import build_mentions, write_mentions

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--audit")
    ap.add_argument("--roster-n", type=int, default=5)
    a = ap.parse_args()
    ro = sqlite3.connect(a.db)
    aw = None; fh = None
    if a.audit:
        fh = open(a.audit, "w", newline=""); aw = csv.writer(fh)
        aw.writerow(["item_id","title","person","basis","confidence"])
    rows = build_mentions(ro, roster_n=a.roster_n, audit_writer=aw)
    if fh: fh.close()
    ro.close()
    print(f"resolved {len(rows)} (item,person) mentions "
          f"for {len({r['node_key'] for r in rows})} people"
          + (f"; audit -> {a.audit}" if a.audit else ""))
    if not a.commit:
        print("DRY-RUN — no DB write. Re-run with --commit to persist."); return 0
    from scripts._area_tag_migrate import hardened_backup
    hardened_backup(a.db, "entity_mentions")
    w = sqlite3.connect(a.db, timeout=10)
    w.execute("PRAGMA busy_timeout=5000")
    try:
        n = write_mentions(w, rows); w.commit()
    finally:
        w.close()
    print(f"COMMITTED {n} mentions.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest v2/tests/test_tag_entity_mentions_cli.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/tag_entity_mentions.py v2/tests/test_tag_entity_mentions_cli.py
git commit -m "feat(entity-mentions): gated tagger runner (dry-run default, --commit, --audit)"
```

---

### Task 5: Serving — addendum payload + render (shared layer)

**Files:**
- Modify: `v2/core/retrieval/structured_answer.py`
- Modify: `bot/config.py` (flags)
- Test: `v2/tests/test_person_addendum.py`

**Interfaces:**
- Produces in `structured_answer.py`:
  - `build_person_addendum(conn, entity_id, *, mentions_on: bool, award_cap: int = 6) -> dict | None` — returns `{"awards": str|None, "prose": {"title","content","url"}|None}` or None. Awards: direct SQL `type='award' AND entity_id=?` (Tier-1), compact `title (year)`, drop bare `^\d{4}$` rows, cap `award_cap` + "+N more". Prose (only if `mentions_on`): join `entity_mentions ⋈ knowledge_items ON stable_key`, `is_active=1`, `nodes.is_active=1`, prefer `match_basis='title'` then newest; take ONE `{title, content verbatim, source_url}`.
  - `render_addendum(payload, *, used_len: int, platform_cap: int) -> str | None` — assembles the verbatim block under `─── More on this person ───`; awards line(s) always; prose WHOLE iff `used_len + len(block) <= platform_cap` else omit prose (optional `More: <title> — <url>` pointer if url). Returns None if nothing to add.
- Flags in `bot/config.py`: `PERSON_ADDENDUM_ENABLED`, `PERSON_MENTIONS_ENABLED`, `EM_ROSTER_N=5`, `AWARD_CAP=6`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_person_addendum.py
from v2.core.database.schema import create_knowledge_schema
from v2.core.retrieval.structured_answer import build_person_addendum, render_addendum

def _db():
    c = create_knowledge_schema(":memory:")
    c.execute("INSERT INTO nodes(type,key,name,is_active) VALUES('Person','k/deek','Deek, Fadi',1)")
    c.execute("INSERT INTO knowledge_items(type,title,content,is_active,created_by,metadata) VALUES"
              "('award','2022 Lifetime Achievement Award, NJIT','x',1,'crawler','{\"entity_id\":\"k/deek\"}')")
    c.execute("INSERT INTO knowledge_items(type,title,content,is_active,created_by,metadata) VALUES"
              "('award','2022','noise-bare-year',1,'crawler','{\"entity_id\":\"k/deek\"}')")
    c.commit(); return c

def test_awards_compact_drop_bare_year():
    p = build_person_addendum(_db(), "k/deek", mentions_on=False)
    assert "Lifetime Achievement" in p["awards"] and "noise-bare-year" not in p["awards"]

def test_prose_whole_if_fits_else_omit():
    payload = {"awards": None, "prose": {"title":"Bio","content":"X"*50,"url":"http://s"}}
    big = render_addendum(payload, used_len=0, platform_cap=4096)
    assert "X"*50 in big                             # whole
    tight = render_addendum(payload, used_len=0, platform_cap=40)
    assert "X"*50 not in tight and ("http://s" in tight or tight is None)  # never partial

def test_none_when_empty():
    assert render_addendum({"awards":None,"prose":None}, used_len=0, platform_cap=2000) is None
```

- [ ] **Step 2: Run to verify fail** — FAIL

- [ ] **Step 3: Implement** (add flags to `bot/config.py`, then the two functions). Flags:

```python
# bot/config.py (module level, near ANSWER_GATE_ENABLED)
_on = lambda v: os.getenv(v, "").strip().lower() in ("1","true","yes","on")
PERSON_ADDENDUM_ENABLED = os.getenv("PERSON_ADDENDUM_ENABLED", "1").strip().lower() in ("1","true","yes","on")
PERSON_MENTIONS_ENABLED = os.getenv("PERSON_MENTIONS_ENABLED", "0").strip().lower() in ("1","true","yes","on")
EM_ROSTER_N = int(os.getenv("EM_ROSTER_N", "5"))
AWARD_CAP = int(os.getenv("AWARD_CAP", "6"))
```

`structured_answer.py`:

```python
import re as _re
_DIVIDER = "─── More on this person ───"
_BARE_YEAR = _re.compile(r"^\d{4}$")

def _award_year(title):
    m = _re.match(r"^(\d{4})\b", title or "")
    return m.group(1) if m else None

def build_person_addendum(conn, entity_id, *, mentions_on, award_cap=6):
    awards = []
    for (title,) in conn.execute(
            "SELECT title FROM knowledge_items WHERE is_active=1 AND type='award' "
            "AND json_extract(metadata,'$.entity_id')=? ORDER BY title DESC", (entity_id,)):
        t = (title or "").strip()
        if not t or _BARE_YEAR.match(t):
            continue
        awards.append(t)
    awards_str = None
    if awards:
        shown = awards[:award_cap]
        extra = f" (+{len(awards)-len(shown)} more)" if len(awards) > len(shown) else ""
        awards_str = "Awards & honors: " + "; ".join(shown) + extra
    prose = None
    if mentions_on:
        row = conn.execute(
            "SELECT k.title, k.content, k.source_url FROM entity_mentions m "
            "JOIN knowledge_items k ON k.is_active=1 AND "
            "  COALESCE(json_extract(k.metadata,'$.natural_key'), 'id:'||k.id)=m.stable_key "
            "JOIN nodes n ON n.id=m.node_id AND n.is_active=1 "
            "WHERE m.node_key=? "
            "ORDER BY (m.match_basis='title') DESC, k.created_at DESC LIMIT 1", (entity_id,)).fetchone()
        if row and (row[1] or "").strip():
            prose = {"title": row[0] or "", "content": row[1].strip(), "url": row[2]}
    if not awards_str and not prose:
        return None
    return {"awards": awards_str, "prose": prose}

def render_addendum(payload, *, used_len, platform_cap):
    if not payload:
        return None
    parts = []
    if payload.get("awards"):
        parts.append(payload["awards"])
    prose = payload.get("prose")
    block_head = f"\n\n{_DIVIDER}\n"
    if prose:
        whole = prose["content"]
        candidate = block_head + ("\n".join(parts) + "\n" if parts else "") + whole \
                    + (f"\nSource: {prose['url']}" if prose.get("url") else "")
        if used_len + len(candidate) <= platform_cap:
            return candidate
        # prose doesn't fit -> NEVER partial: awards (if any) + optional pointer
        tail = (f"\nMore: {prose['title']} — {prose['url']}" if prose.get("url") else "")
        if parts or tail:
            return block_head + "\n".join(parts) + tail
        return None
    if parts:
        return block_head + "\n".join(parts)
    return None
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest v2/tests/test_person_addendum.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/structured_answer.py bot/config.py v2/tests/test_person_addendum.py
git commit -m "feat(entity-mentions): person_addendum payload + verbatim length-budgeted render"
```

---

### Task 6: Wire the addendum into the two worker threads + `_compose_structured` (R1/R4)

**Files:**
- Modify: `bot/core/message_handler.py` (`_run` inside `_try_structured` ~:550, `_structured_from_route` ~:649, `_compose_structured` ~:588, and the three callers `_answer_decision` ~:707, `_try_structured` ~:583, `_resume_pending` ~:646)
- Test: `v2/tests/test_addendum_wiring.py`

**Interfaces:**
- `_compose_structured(self, text, facts, suffix, deterministic, addendum=None, platform=None)` — after building `out` (compose + suffix), append `render_addendum(addendum, used_len=len(out), platform_cap=_cap(platform))`.
- `_cap(platform) -> int`: `4096 if platform=="telegram" else 2000`.
- Both thread bodies compute `addendum = build_person_addendum(conn, rt.args.get("entity_id"), mentions_on=botcfg.PERSON_MENTIONS_ENABLED)` when `botcfg.PERSON_ADDENDUM_ENABLED` and `rt.skill in ("entity_card","research_of_person")` and an entity_id is present, else None; return it as an extra tuple element.

- [ ] **Step 1: Write the failing test** (flag-off byte-identical; flag-on appends awards)

```python
# v2/tests/test_addendum_wiring.py  (unit-level on _compose_structured + _cap)
import asyncio
from bot.core.message_handler import MessageHandler

def test_cap_platform():
    h = MessageHandler.__new__(MessageHandler)
    assert h._cap("telegram") == 4096 and h._cap("discord") == 2000 and h._cap(None) == 2000

def test_compose_appends_addendum(monkeypatch):
    h = MessageHandler.__new__(MessageHandler)
    h.ollama = None
    out = asyncio.run(h._compose_structured("q", "CARD", "", True,
                        addendum={"awards":"Awards & honors: X","prose":None}, platform="discord"))
    assert out.startswith("CARD") and "Awards & honors: X" in out

def test_compose_no_addendum_unchanged():
    h = MessageHandler.__new__(MessageHandler); h.ollama = None
    out = asyncio.run(h._compose_structured("q","CARD","",True, addendum=None, platform="discord"))
    assert out == "CARD"
```

- [ ] **Step 2: Run to verify fail** — FAIL (signature/`_cap` missing)

- [ ] **Step 3: Implement wiring**

In `_compose_structured` add params + append step (after the existing `out`/suffix logic, before return):

```python
async def _compose_structured(self, text, facts, suffix, deterministic,
                              addendum=None, platform=None):
    out = facts
    if self.ollama and not deterministic:
        composed = await self.ollama.compose_from_rows(text, facts)
        if composed and _compose_preserves_facts(facts, composed):
            out = composed
    if suffix:
        out = f"{out}\n\n{suffix}"
    if addendum:
        from v2.core.retrieval.structured_answer import render_addendum
        add = render_addendum(addendum, used_len=len(out), platform_cap=self._cap(platform))
        if add:
            out = f"{out}{add}"
    return out

def _cap(self, platform):
    return 4096 if platform == "telegram" else 2000
```

In `_run` (inside `_try_structured`) and `_structured_from_route`, after `facts` is built and non-empty, compute the payload and add to the returned tuple:

```python
        addendum = None
        if botcfg.PERSON_ADDENDUM_ENABLED and rt.skill in ("entity_card", "research_of_person"):
            eid = (rt.args or {}).get("entity_id")
            if eid:
                from v2.core.retrieval.structured_answer import build_person_addendum
                addendum = build_person_addendum(conn, eid,
                              mentions_on=botcfg.PERSON_MENTIONS_ENABLED, award_cap=botcfg.AWARD_CAP)
        return (rt, facts, structured_answer.deterministic_suffix(result),
                structured_answer.is_deterministic(result),
                structured_answer.person_names_of(result), addendum)
```

(`_structured_from_route` returns the 4-tuple variant → add `addendum` there too; it builds `Route(skill, args)` so use that as `rt`.)

Update the three unpack sites + `_compose_structured` calls to pass `addendum` and `platform=req.platform` (in `_answer_decision`) / the request platform threaded into `_try_structured`. `_try_structured` gains a `platform: str | None = None` param; `handle()` passes `req.platform`. `_resume_pending` passes the stored platform or None (Telegram-origin → its platform).

- [ ] **Step 4: Run tests** — `python -m pytest v2/tests/test_addendum_wiring.py v2/tests/ -q` → PASS; then full suite `python -m pytest v2/tests -q`.

- [ ] **Step 5: Commit**

```bash
git add bot/core/message_handler.py v2/tests/test_addendum_wiring.py
git commit -m "feat(entity-mentions): wire addendum through worker threads + platform-aware compose"
```

---

### Task 7: Gold gate + eval questions + goals checklist

**Files:**
- Create: `scripts/eval_entity_mentions.py`
- Modify: `eval/questions.txt`
- Modify: `docs/superpowers/specs/2026-07-07-person-entity-mentions-tagging-design.md` (§14 checklist)
- Test: `v2/tests/test_entity_mentions_gold.py`

**Interfaces:**
- `eval_entity_mentions.py`: loads a small labeled set (accept: id=64 bio, an MMI news, a Deek memoir multi-person; reject: a Ph.D.-roster page, a Scholar co-author "Personal website"), runs `resolve_item`, prints precision/recall, exits non-zero if precision < 0.9.

- [ ] **Step 1: Write the gold test**

```python
# v2/tests/test_entity_mentions_gold.py
from v2.core.ingestion.entity_mentions import resolve_item, PersonName
ORIA = PersonName(1,"k/oria","Oria","Vincent")
def others(n): return [PersonName(10+i,f"k/o{i}",f"L{i}",f"F{i}") for i in range(n)]

CASES = [
    # (title, content, people, should_accept_oria)
    ("Who is Prof. Vincent Oria?", "Vincent Oria is a Professor and Chair.", [ORIA], True),
    ("MMI 2026", "Committee: Vincent Oria (NJIT).", [ORIA], True),
    ("Award", "2010 Franklin V. Taylor Memorial Award", [ORIA], False),   # 'oria' in memorial
    ("Ph.D. Computer Science", "Professor Oria, Vincent " + " ".join(f"F{i} L{i}" for i in range(6)),
     [ORIA]+others(6), False),                                           # roster
]
def test_gold_precision():
    tp=fp=fn=0
    for title, body, people, want in CASES:
        got = any(p.node_key=="k/oria" for p,_,_ in resolve_item(title, body, people))
        if want and got: tp+=1
        elif got and not want: fp+=1
        elif want and not got: fn+=1
    prec = tp/(tp+fp) if tp+fp else 1.0
    assert prec >= 0.9, f"precision {prec}"
```

- [ ] **Step 2: Run** — should PASS given Task 2's gate. If FAIL, fix the gate, not the test.

- [ ] **Step 3: Write `scripts/eval_entity_mentions.py`** (same CASES, prints metrics, `sys.exit(1)` if precision<0.9). Add to `eval/questions.txt` under a new `# person enrichment` header:

```
# person enrichment (entity-mentions tagging)
who is Vincent Oria
what awards has Fadi Deek won
is Vincent Oria involved in the MMI workshop
```

- [ ] **Step 4: Fill the spec §14 goals checklist** (mark G1–G6 shipped; deferred items loud).

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_entity_mentions.py eval/questions.txt v2/tests/test_entity_mentions_gold.py docs/superpowers/specs/2026-07-07-person-entity-mentions-tagging-design.md
git commit -m "test(entity-mentions): gold precision gate + eval questions + goals checklist"
```

---

## Deploy (after all tasks pass — a SEPARATE gated step, not a task)

1. `python -m pytest v2/tests -q` (full suite green).
2. Dev-copy tag: `cp gsa_gateway.db /tmp/dev.db; python scripts/tag_entity_mentions.py --db /tmp/dev.db --audit /tmp/audit.csv` → **owner eyeballs `/tmp/audit.csv`** (Tier-2 precision) BEFORE live.
3. Live tag (gated): `python scripts/tag_entity_mentions.py --commit --audit out/em_audit.csv` (hardened_backup auto).
4. Deploy code: `bash scripts/restart.sh`. At merge `PERSON_ADDENDUM_ENABLED=1` (awards live), `PERSON_MENTIONS_ENABLED=0`.
5. After the audit passes: flip `PERSON_MENTIONS_ENABLED=1` + restart.
6. `$0` cached Set-A re-run → confirm person-query debt dropped.

## Self-review notes
- Spec coverage: G1 (T1–T4), G2 awards (T5/T6), G2 prose (T5/T6, flag-gated), G3 budget (T5 render + T6 post-compose measure), G4 flags (T5), G5 RAG untouched (no retriever edit), G6 Set-A (deploy step). §15 R1–R12 each mapped.
- Deferred (loud): topic tagging, students/staff, LLM-verify default, multi-item prose, dashboard job.

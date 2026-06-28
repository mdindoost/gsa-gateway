# Split-Ops Build 1 — Schema Split + Config Plumbing (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Implement task-by-task, one failing test at a time. Steps use `- [ ]`.
> **Spec:** `docs/superpowers/specs/2026-06-28-split-ops-db-design.md` (read §2, §3.1, §3.2, and Touch Points first).
> **This is Phase 1 of 5.** It is ADDITIVE — it must NOT move/copy/drop any data and must NOT change runtime publishing/judging behavior. The migration that moves data is Phase 5.

**Goal:** Split the v2 schema builder into `create_knowledge_schema` / `create_ops_schema`, add `get_ops_connection` + `operations_db_path` config, give the OPS publishing tables an `org_slug` column, make the OPS `events` table match the LIVE shape, and retire the monolithic `create_all` from every Knowledge-DB startup path so dropped-tables can't silently reappear (spec HIGH-3).

**Architecture:** Two SQLite files, two connections (no ATTACH). `gsa_gateway.db` = knowledge/KG; `gsa_gateway_ops.db` = publishing cluster + judging. This phase only builds the schema seams + config; nothing reads/writes the OPS DB at runtime yet (that's Phase 2).

**Tech stack:** Python 3.11, stdlib `sqlite3`, pytest. STRICT tables where the originals are STRICT; the OPS `events` table is intentionally NON-STRICT/AUTOINCREMENT (matches live).

## Global Constraints (apply to every task)
- **Additive only.** No data move/drop in this phase. No `ALTER`/`DROP` against the live DB.
- **No new pip deps.**
- **No Claude/AI attribution in commits.**
- **Hard line — moved tables must NOT be creatable on the Knowledge DB after this phase.** The set of MOVED tables is exactly: `posts`, `post_templates`, `post_deliveries`, `events`, `event_reminders`, `judging_events`, `judging_judges`, `judging_presenters`, `judging_scores`, `judging_audience_votes`, `judging_score_audit`. Everything else STAYS in knowledge.
- **OPS publishing tables carry `org_slug TEXT NOT NULL`** (`posts`, `events`, `post_templates`) and retain `org_id` as a plain INTEGER (NO FK). Judging tables are unchanged (no org ref).
- **OPS `events` = LIVE v1 shape** (`INTEGER PRIMARY KEY AUTOINCREMENT`, columns incl. `announcement_sent`, `channel_posted`) + `org_slug` + retained `org_id` (no FK). NOT the dead STRICT v2 DDL.
- Run the full existing suites green before claiming done: `python3 -m pytest v2/tests -q` and `python3 -m pytest bot/tests -q`.
- **L2 reporting:** write your working notes + final report to `docs/superpowers/plans/split-ops/build-1-report.md` (this file is YOURS — no other agent writes it). Do NOT write to any memory file or to `BUILD_LEDGER.md` (the orchestrator owns those).

---

## File Structure
- **Modify** `v2/core/database/schema.py` — partition `_TABLE_DDL`/`INDEXES`/`_COLUMN_MIGRATIONS`/`_POST_MIGRATION_INDEXES` into knowledge vs ops groups; add `create_knowledge_schema`, `create_ops_schema`, `get_ops_connection`; add OPS DDL constants (`OPS_POSTS`, `OPS_EVENTS`, `OPS_POST_TEMPLATES` with `org_slug`); keep `create_all` as a thin wrapper that calls both (back-compat for tests).
- **Modify** `bot/config.py` — add `operations_db_path` field + env default.
- **Modify** `v2/local_server.py:1043` — replace `create_all(DB_PATH)` with `create_knowledge_schema(DB_PATH)` + `create_ops_schema(OPS_DB_PATH)`; add `OPS_DB_PATH` next to `DB_PATH:34`.
- **Modify** `bot/services/database.py` — remove the `events` CREATE from `init_tables` (`:110`) and the `migrate_events_columns()` call (`:146`); `bot/main.py:96` call too. (`events` is owned by `create_ops_schema`.)
- **Create** `v2/tests/test_schema_split.py` — the invariant + builder tests.

---

## Task 1: Partition the schema into knowledge vs ops builders

**Files:** Modify `v2/core/database/schema.py`; Test `v2/tests/test_schema_split.py`.

**Interfaces — Produces (later phases rely on these exact names):**
- `create_knowledge_schema(db_path: str) -> sqlite3.Connection` — creates ONLY knowledge/KG tables, indexes, triggers, the FTS, the settings seed, schema_migrations. Loads sqlite-vec.
- `create_ops_schema(db_path: str) -> sqlite3.Connection` — creates ONLY the moved tables + their indexes + the posts/post_deliveries/judging column-migrations + `idx_posts_delete_due`. Does NOT load sqlite-vec.
- `get_ops_connection(db_path: str) -> sqlite3.Connection` — like `get_connection` (row_factory, `foreign_keys=ON`, `busy_timeout=5000`) but WITHOUT `load_sqlite_vec`.
- `create_all(db_path)` retained: calls `create_knowledge_schema` then `create_ops_schema` against the SAME path (back-compat; used only by tests/fixtures that want one combined DB).
- OPS DDL constants: `OPS_POSTS`, `OPS_EVENTS`, `OPS_POST_TEMPLATES` (see constraints — add `org_slug TEXT NOT NULL` after `org_id`; OPS_EVENTS uses the live AUTOINCREMENT shape). `OPS_POSTS`/`OPS_POST_TEMPLATES` are the existing STRICT DDL with `org_slug TEXT NOT NULL` added and the `REFERENCES organizations(id)` dropped from `org_id`.

- [ ] **Step 1 — failing test: builders create disjoint table sets.**
```python
# v2/tests/test_schema_split.py
import sqlite3, tempfile, os
from v2.core.database import schema

MOVED = {"posts","post_templates","post_deliveries","events","event_reminders",
         "judging_events","judging_judges","judging_presenters","judging_scores",
         "judging_audience_votes","judging_score_audit"}

def _tables(path):
    c = sqlite3.connect(path)
    rows = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    return rows

def test_knowledge_schema_has_no_moved_tables(tmp_path):
    p = str(tmp_path/"k.db")
    schema.create_knowledge_schema(p).close()
    assert _tables(p).isdisjoint(MOVED)            # HIGH-3 invariant
    assert "knowledge_items" in _tables(p) and "organizations" in _tables(p)

def test_ops_schema_has_exactly_moved_tables(tmp_path):
    p = str(tmp_path/"o.db")
    schema.create_ops_schema(p).close()
    assert MOVED.issubset(_tables(p))
    assert "knowledge_items" not in _tables(p) and "nodes" not in _tables(p)
```
- [ ] **Step 2 — run, verify it fails** (`AttributeError: create_knowledge_schema`). `python3 -m pytest v2/tests/test_schema_split.py -q`
- [ ] **Step 3 — implement** the partition: split `_TABLE_DDL` → `_KNOWLEDGE_TABLE_DDL` + `_OPS_TABLE_DDL` (ops = OPS_POSTS, OPS_POST_TEMPLATES, POST_DELIVERIES, OPS_EVENTS, EVENT_REMINDERS, JUDGING_*). Split `INDEXES` → knowledge vs ops (ops gets the posts/tmpl/deliv/events/remind/judging indexes incl. `idx_judging_one_open`). Split `_COLUMN_MIGRATIONS` (ops gets posts/post_deliveries/judging rows; knowledge keeps `frontier.error`). `_POST_MIGRATION_INDEXES` (idx_posts_delete_due) → ops. The settings seed + triggers + FTS stay knowledge. Define `create_knowledge_schema`/`create_ops_schema`/`get_ops_connection` and the OPS DDL constants. Keep `create_all` as the two-call wrapper.
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** (`feat: split v2 schema into knowledge + ops builders`).

## Task 2: OPS publishing tables carry org_slug; events matches live shape

**Files:** Modify `v2/core/database/schema.py`; Test `v2/tests/test_schema_split.py`.

- [ ] **Step 1 — failing test:**
```python
def _cols(path, table):
    c = sqlite3.connect(path); info = c.execute(f"PRAGMA table_info({table})").fetchall(); c.close()
    return {row[1]: row for row in info}   # name -> (cid,name,type,notnull,dflt,pk)

def test_ops_posts_events_templates_have_org_slug(tmp_path):
    p = str(tmp_path/"o.db"); schema.create_ops_schema(p).close()
    for t in ("posts","events","post_templates"):
        cols = _cols(p, t)
        assert "org_slug" in cols and cols["org_slug"][3] == 1   # NOT NULL
        assert "org_id" in cols                                   # retained

def test_ops_events_is_live_shape(tmp_path):
    p = str(tmp_path/"o.db"); schema.create_ops_schema(p).close()
    cols = _cols(p, "events")
    assert {"announcement_sent","channel_posted"} <= set(cols)    # legacy cols preserved
    # AUTOINCREMENT events registers in sqlite_sequence after first insert
```
- [ ] **Step 2 — run, verify fail.**
- [ ] **Step 3 — implement** `OPS_POSTS`/`OPS_POST_TEMPLATES` = the existing STRICT DDL with `org_id INTEGER` (no FK) + `org_slug TEXT NOT NULL`. `OPS_EVENTS` = the LIVE shape:
```sql
CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    time              TEXT    NOT NULL DEFAULT 'TBD',
    location          TEXT    NOT NULL DEFAULT 'TBD',
    description       TEXT    NOT NULL DEFAULT '',
    organizer         TEXT    NOT NULL DEFAULT 'GSA',
    rsvp_link         TEXT    NOT NULL DEFAULT '',
    category          TEXT    NOT NULL DEFAULT 'general',
    reminder_sent_7d  INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1d  INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1h  INTEGER NOT NULL DEFAULT 0,
    announcement_sent INTEGER NOT NULL DEFAULT 0,
    channel_posted    TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by        TEXT    NOT NULL DEFAULT 'system',
    org_id            INTEGER,
    org_slug          TEXT    NOT NULL DEFAULT 'gsa'
);
```
(`event_reminders.event_id → events(id)` still resolves — events keeps an INTEGER PK. The `DEFAULT 'gsa'` on org_slug is a convenience for fresh inserts; the migration sets it explicitly per row.)
- [ ] **Step 4 — run, verify pass.**
- [ ] **Step 5 — commit** (`feat: ops posts/events/templates carry org_slug; events matches live shape`).

## Task 3: `operations_db_path` config

**Files:** Modify `bot/config.py:19,88`; Test `bot/tests/test_config.py` (or add to existing config test).

- [ ] **Step 1 — failing test:** `operations_db_path` defaults to a sibling of `database_path` (`./gsa_gateway_ops.db`) and honors `OPERATIONS_DB_PATH`.
- [ ] **Step 2 — verify fail.**
- [ ] **Step 3 — implement:** add `operations_db_path: str` to the dataclass; in the factory, `operations_db_path=os.getenv("OPERATIONS_DB_PATH", _sibling(database_path, "gsa_gateway_ops.db"))` where `_sibling` swaps the filename of `database_path`.
- [ ] **Step 4 — verify pass.**
- [ ] **Step 5 — commit** (`feat: add operations_db_path config`).

## Task 4: Retire `create_all` from Knowledge startup paths (HIGH-3)

**Files:** Modify `v2/local_server.py:32-34,1043`; `bot/services/database.py:110,146`; `bot/main.py:96`; Test `v2/tests/test_schema_split.py`, adjust `bot/tests/conftest.py` / `bot/tests/test_database.py` if they relied on `events`.

- [ ] **Step 1 — failing test:** simulate the server startup schema call against a temp knowledge DB + temp ops DB and assert the knowledge DB has none of `MOVED`. (Add `test_server_startup_keeps_moved_tables_out_of_knowledge` driving the same `create_knowledge_schema` + `create_ops_schema` the server `main` will call.)
- [ ] **Step 2 — verify fail** (today `create_all` would create them).
- [ ] **Step 3 — implement:** add `OPS_DB_PATH = Path(os.getenv("OPERATIONS_DB_PATH", str(REPO_ROOT / "gsa_gateway_ops.db")))` near `DB_PATH`; in `main()` replace `create_all(str(DB_PATH)).close()` with `create_knowledge_schema(str(DB_PATH)).close()` + `create_ops_schema(str(OPS_DB_PATH)).close()`. In `bot/services/database.py`, delete the `events` CREATE from the `init_tables` SQL script and remove the `self.migrate_events_columns()` call at `:146` (and the now-dead method, or leave it unused — prefer remove); remove `bot/main.py:96` `self.db.migrate_events_columns()`. Update any bot test/conftest that expected `events` to exist on the bot Database (point those at `create_ops_schema` or drop the dependency). **Note:** v1 event read/write that still targets the knowledge conn is repointed in Phase 2; in this phase the live DB still has `events` (additive), so nothing breaks at runtime.
- [ ] **Step 4 — verify pass:** `python3 -m pytest v2/tests -q` AND `python3 -m pytest bot/tests -q` both green.
- [ ] **Step 5 — commit** (`fix: retire create_all from knowledge startup paths so moved tables can't reappear (HIGH-3)`).

---

## Self-review checklist (run before reporting done)
1. `create_knowledge_schema` output ∩ MOVED == ∅ (invariant test green).
2. `create_ops_schema` output ⊇ MOVED, and has no knowledge tables.
3. `posts`/`events`/`post_templates` in OPS have `org_slug NOT NULL` + retained `org_id` (no FK).
4. OPS `events` has `announcement_sent`/`channel_posted` + AUTOINCREMENT.
5. No knowledge startup path calls the monolithic `create_all`.
6. Full `v2/tests` + `bot/tests` suites green; judging suite (49) still green.
7. Additive only — `git diff` shows zero data moves/drops; no live-DB writes.

## Report (write to `docs/superpowers/plans/split-ops/build-1-report.md`)
- Commits made (hashes + messages).
- Exact final signatures of `create_knowledge_schema`/`create_ops_schema`/`get_ops_connection` and the OPS DDL column lists (Phase 2 needs these).
- Any bot test you had to adjust for the events-creation removal, and why.
- Test counts before/after. Anything deferred or surprising.

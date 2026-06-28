# Build-2 Review Findings Fix Report

**Date:** 2026-06-28  
**Branch:** `worktree-split-ops-db`  
**Commit:** `6563686`  
**Baseline:** `dfa8727` (pre-fix branch tip)

---

## Evidence-Based Verification

Before touching any code, the baseline test failure NAME SETS were captured:

**v2/tests (baseline):** 44 failed, 1167 passed, 73 errors — all in
`test_office_routing_gold.py` and `test_rerank_gold_chunks.py` (require live
Ollama + populated DB; pre-existing in this worktree's isolated env).

**bot/tests (baseline):** 12 failed —
`test_control_api::test_health_reports_departments_from_registry`,
`test_departments::test_supported_is_cs_and_ds_today`,
`test_router::test_descriptive_questions_are_not_routed[who is the dean]`,
`test_router_v21_flags::test_router_v21_defaults_off`,
and 8 `test_worldcup.py` tests (event-loop / config issues; pre-existing).

**After fixes:**

- `v2/tests`: 44 failed, 1168 passed (+1 new test), 73 errors — **zero net-new
  failures; same pre-existing names.**
- `bot/tests`: 12 failed, 519 passed — **identical failure set to baseline.**
- Judging suite (`test_judging_db`, `test_judging_calculator`,
  `test_judging_session`): **99/99 pass.**
- `migrate_events_columns()` grep: **0 remaining call sites** in the 5 scripts.
- `resolve_org`/`OrgCache` grep confirms wired in `match_watcher.py`,
  `bot/main.py` (fixtures + failure digest), `scheduler.py` — no longer dead code.

---

## F1 — FULLY FIXED

**migrate_events_columns() crashes on next restart.**

- Added `migrate_events_columns()` no-op stub to `Database` in
  `bot/services/database.py` (deprecation comment, `pass` body).
- Removed the 5 call sites: `run_telegram.py`, `run_groupme.py`,
  `scripts/eval_run.py`, `scripts/_eval_kb_100.py`, `scripts/trace_query.py`.
- Syntax check: `python3 -c "import ast,sys; [ast.parse(open(f).read()) for f in
  ['run_telegram.py','run_groupme.py']]"` → OK.

## F2 — FULLY FIXED

**Judging NOT repointed to OPS.**

- `run_telegram.py`: `JudgingSessionManager(db_path=str(config.operations_db_path))`.
- `run_groupme.py`: does NOT wire judging (no change needed; confirmed).
- `v2/local_server.py`: added `_ops_conn()` method (opens `OPS_DB_PATH`);
  replaced `self._conn()` with `self._ops_conn()` in:
  - `do_GET` `/judging/events` block
  - `_judging_get()` handler
  - `_judging_post_events()` handler
  - `_judging_post_event()` handler
- NOTE: The `/db` sql.js snapshot stays Knowledge-only (Build 4's job); this
  fix is the server-side LIVE judging API repoint only, as specified.

## F3 — FULLY FIXED

**failure-digest SourceRunner old single-conn signature.**

`bot/main.py` failure-digest block now:
- Opens `kb_conn = get_connection(config.database_path)` and
  `ops_conn = get_ops_connection(config.operations_db_path)` separately.
- Uses `resolve_org(kb_conn, org_slug)` for the org lookup (enforces LOW-11;
  raises ValueError on missing/ambiguous — logged and skips).
- Calls `SourceRunner(ops_conn, kb_conn, source, interval=3600)`.

## F4 — FULLY FIXED

**Materializers insert posts without org_slug.**

`v2/core/publishing/scheduler.py`:
- `materialize_templates`: INSERT now includes `org_slug` column, stamped
  from `t["org_slug"]`.
- `materialize_event_reminders`: SELECT now fetches `e.org_slug AS ev_org_slug`;
  INSERT stamps it.

Tests: added `assert posts[0]["org_slug"] == "gsa"` assertions to both
`test_scheduler_materializes_templates_on_ops` and
`test_scheduler_materialize_reminders_on_ops` in `test_build2_split_ops.py`.

## F5 — FULLY FIXED

**bot/services/database.py events CRUD reads/writes KB on a LIVE food path.**

`bot/services/database.py`:
- `Database.__init__(db_path, ops_db_path=None)`: new optional param. When
  provided, `connect()` opens a separate OPS connection via `create_ops_schema`
  (so OPS tables exist). None = combined mode (OPS routes to `self._conn`).
- `_ops_conn` property returns `self.__ops_conn` (split) or `self._conn` (combined).
- `close()` closes both connections.
- `add_event`, `get_events_for_reminders`, `get_upcoming_events_db`,
  `get_all_events`, `mark_reminder_sent`, `mark_announcement_sent` all use
  `self._ops_conn` instead of `self.conn`.

Callers updated:
- `run_telegram.py`: `Database(config.database_path, ops_db_path=str(config.operations_db_path))`
- `run_groupme.py`: same.

`bot/tests/conftest.py`: removed the OPS_EVENTS-on-KB masking bridge. Now uses
`Database(":memory:", ops_db_path=":memory:")` which opens two separate in-memory
DBs. `connect()` runs `create_ops_schema` on the OPS one. The food path
(`message_handler:602 → food_detector.get_food_events → get_upcoming_events_db`)
is correctly exercised against the OPS connection even in tests.

## F6 — FULLY FIXED (resolve_org + OrgCache wired; no dead code)

**resolve_org/OrgCache dead code; raw slug fetchone sites.**

- `v2/core/publishing/scheduler.py` `tick()`: creates and clears a per-tick
  `OrgCache` at the top of each tick (MED-7).
- `v2/integration/match_watcher.py` `start()`: replaced raw
  `SELECT id ... WHERE slug=? fetchone()` with `resolve_org(self._kb_conn,
  self.org_slug)` — fails loudly on >1 match (LOW-11).
- `bot/main.py` fixtures digest: replaced raw fetchone with `resolve_org`.
- `bot/main.py` failure digest: replaced raw fetchone with `resolve_org`.

`resolve_org` and `OrgCache` are now called in 4 production paths; no longer
dead code.

NOTE: The publisher's `_platforms`/`_discord_channel`/`_telegram_channel` still
read settings via `row["org_id"]` (the informational stored value, not the slug).
Per §3.2, `org_id` is retained as an informational integer that matches the KB
value at enqueue time; the slug is the durable contract key for cross-DB
references. The publisher reading org_id for settings is SAFE for all current
data. The spec's "resolve `row["org_slug"]`→id in publisher settings reads" is
a MED-7 hot-path optimization that was not included in this fix round — flagged
explicitly: **the publisher settings reads via org_id are correct for current data
but do not use the slug contract; a future hardening pass should add OrgCache to
PostPublisher.__init__ and resolve slug→id there.** The F6 findings' acceptance
criterion (resolve_org not dead code; LOW-11 enforced on live paths) is met.

## F7 — FULLY FIXED

**match_watcher.start() leaks OPS conn if KB open raises.**

Both `get_ops_connection` and `get_connection` calls are now inside the `try`
block. The `except` handler closes both (with None guards).

## F8 — FULLY FIXED

**Weak watcher test.**

`test_match_watcher_start_resolves_org_from_kb`: now exercises real `await
start()` via `asyncio.get_event_loop().run_until_complete(watcher.start())` with
`watcher._loop` patched to a noop coroutine and `asyncio.create_task` patched.
Asserts `watcher.org_id == 1` (org resolved from KB).

`test_match_watcher_start_duplicate_slug_raises` (new test): inserts a second
org with slug `gsa` under a different parent, then asserts `start()` raises
`ValueError` matching `">1 org"` (LOW-11 via `resolve_org`).

---

## Deferred / Phase 3+5 Notes

- **Publisher settings reads via org_slug + OrgCache**: the publisher's
  `_platforms`/`_discord_channel`/etc. still use `row["org_id"]` for settings
  reads. This is safe today but is not yet the slug-contract path. Phase 3 or a
  dedicated hardening should add `OrgCache` to `PostPublisher.__init__` and
  resolve `row["org_slug"]` via it. The N+1 concern (MED-7) exists on the hot
  path but only for multi-org deploys (today all live data is org_id=1 gsa).

- **`/db` sql.js snapshot**: stays KB-only (Build 4's job). The judging tab
  uses live `/judging/*` API endpoints, which are now correctly OPS-routed.

- **`get_events_for_reminders`**: also routed to `_ops_conn` (correct, belt-and-
  suspenders — this method is used by the legacy reminder flow).

---

## Commit Summary

Single commit `6563686` on `worktree-split-ops-db`:
- 12 files changed, 178 insertions(+), 85 deletions(-)
- All F1–F8 findings: FULLY FIXED (with one sub-item of F6 explicitly deferred
  and documented above).

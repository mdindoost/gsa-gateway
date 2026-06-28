# Build 2 Report — Repoint Subsystems to Two Connections

**Date:** 2026-06-28
**Branch:** `worktree-split-ops-db`
**Commit:** `6306c83`

---

## Commits

| Hash | Message |
|------|---------|
| `6306c83` | feat(split-ops): repoint publishing/judging/worldcup to two-connection model (Build 2) |

---

## Final Two-Connection Signatures

### `resolve_org(kb_conn, slug) -> sqlite3.Row`
**Location:** `v2/core/publishing/org_resolve.py`
Queries `organizations WHERE slug=?` on kb_conn. Raises `ValueError("no org with slug '...'")` when 0 rows; raises `ValueError(">1 org with slug '...' (N matches)")` when >1 (LOW-11 enforcement). Phase 3's `event_projection.py` MUST reuse this module, not define its own.

### `class OrgCache`
**Location:** `v2/core/publishing/org_resolve.py`
- `get(kb_conn, slug) -> sqlite3.Row` — memoizes resolve_org per slug within a tick
- `clear()` — flushes cache (call at tick start); `._cache: dict[str, sqlite3.Row]`

### `enqueue_post(ops_conn, kb_conn, draft, *, allowed_channels=None) -> int`
**Location:** `v2/core/publishing/sources.py`
- `kb_conn`: validates org (`organizations.is_active`); resolves `org_slug = org["slug"]`
- `ops_conn`: dedup query + INSERT into `posts` with `org_slug` explicitly set (never relies on DEFAULT 'gsa')
- Combined-file mode: `enqueue_post(conn, conn, draft)` — both params same connection

### `class SourceRunner`
**Location:** `v2/core/publishing/sources.py`
`SourceRunner(ops_conn, kb_conn, source, *, interval=60, allowed_channels=None)`
- `self.conn = ops_conn` (back-compat alias)
- `self._kb_conn = kb_conn`

### `class PostPublisher`
**Location:** `v2/core/publishing/publisher.py`
`PostPublisher(ops_conn, kb_conn, registry, signatures)`
- `self.conn = ops_conn` (posts reads/writes)
- `self._kb_conn = kb_conn` (settings reads: `default.platforms`, `default.channel.*`, `org.*`)
- Combined-file mode: `PostPublisher(conn, conn, registry, sigs)`

### `class SignatureService`
**Location:** `v2/core/publishing/signature.py` — **unchanged**
`SignatureService(kb_conn)` — already took a single conn; wired with kb_conn by callers

### `class Scheduler`
**Location:** `v2/core/publishing/scheduler.py`
`Scheduler(ops_conn, kb_conn, publisher, registry=None)`
- `self.conn = ops_conn` (post_templates, events, event_reminders, posts reads/writes)
- `self._kb_conn = kb_conn` (reserved for Phase 3 org/settings use)
- Combined-file mode: `Scheduler(conn, conn, publisher)`

### `class PostDeleter`
**Location:** `v2/core/publishing/deleter.py` — **unchanged**
`PostDeleter(conn, registry)` — receives ops_conn from Scheduler

### `class ConnectorRegistry`
**Location:** `v2/core/connectors/registry.py` — **unchanged**
`registry.conn = ops_conn` is set by `SchedulerRunner.start()` so deliveries land in OPS

### `class SchedulerRunner`
**Location:** `v2/integration/scheduler_runner.py`
`SchedulerRunner(ops_path, kb_path, registry, interval=30)`
- `start()` opens `get_ops_connection(ops_path)` → `self._ops_conn`; `get_connection(kb_path)` → `self._kb_conn`
- sets `registry.conn = self._ops_conn` (deliveries on OPS)
- Combined-file mode: `SchedulerRunner(db_path, db_path, registry)`

### `class MatchWatcher`
**Location:** `v2/integration/match_watcher.py`
`MatchWatcher(keys, ops_path, kb_path=None, org_slug="gsa", channel="...", state_file=None)`
- `kb_path` defaults to `ops_path` when omitted (backward-compat combined-file mode)
- `start()` opens `get_ops_connection(ops_path)` → `self._conn`; `get_connection(kb_path)` → `self._kb_conn`
- org lookup from `self._kb_conn`; `auto_delete_hours` from `self._kb_conn`; `enqueue_post` writes to `self._conn`
- `self.db_path = self.ops_path` (back-compat alias for callers reading `.db_path`)

### `class EspnMatchWatcher`
**Location:** `v2/integration/wc_providers/watcher.py`
`EspnMatchWatcher(keys, ops_path, kb_path=None, org_slug="gsa", channel="...", state_file=None, provider=None)`
Inherits both-path logic from MatchWatcher.

### `make_watcher(keys, ops_path, kb_path=None, org_slug="gsa", channel="...", state_file=None)`
**Location:** `v2/integration/wc_providers/watcher.py`

### `class JudgingSessionManager`
**Location:** `v2/core/judging/session.py` — **unchanged signature**
`JudgingSessionManager(ops_path)` — already took `db_path`; callers (bot/main.py) will pass `operations_db_path`. No class change required.

---

## Two-DB Fixture (for Phase 3 and Phase 5 reuse)

**Location:** `v2/tests/test_build2_split_ops.py`

Two fixtures:

### `two_db` fixture (separate KB + OPS temp files)
```python
@pytest.fixture()
def two_db(tmp_path):
    """Separate KB and OPS temp-file DBs with a seeded GSA org + settings."""
    kb_path = str(tmp_path / "kb.db")
    ops_path = str(tmp_path / "ops.db")
    kb_conn = create_knowledge_schema(kb_path)
    ops_conn = create_ops_schema(ops_path)
    # Seeds: GSA org in KB, settings in KB
    yield {"kb_conn": kb_conn, "ops_conn": ops_conn,
           "kb_path": kb_path, "ops_path": ops_path}
```
Use for genuine two-DB tests. Phase 3 can extend it with event inserts.

### `combined_db` fixture (one `:memory:` via `create_all`)
```python
@pytest.fixture()
def combined_db():
    """Combined DB — ops_path == kb_path, behavior-preserving net."""
    conn = create_all(":memory:")
    # Seeds: GSA org + settings on same connection
    yield conn
```
Use for regression tests proving combined-file mode unchanged.

---

## Tests Updated to New Signature

All updated to pass `(conn, conn, ...)` in combined-file mode (same conn for ops+kb):

| File | Change | Reason |
|------|--------|--------|
| `v2/tests/test_publisher.py` | `PostPublisher(conn, conn, registry, sigs)` + `Scheduler(conn, conn, publisher)` | Old single-conn signatures |
| `v2/tests/test_sources.py` | `enqueue_post(conn, conn, draft)` + `SourceRunner(conn, conn, source)` throughout | Old single-conn signatures |
| `v2/tests/test_scheduler_delete_tick.py` | `Scheduler(conn, conn, publisher)` (×2) | Old single-conn signature |
| `v2/tests/test_enqueue_delete_at.py` | `enqueue_post(conn, conn, draft)` (×2) | Old single-conn signature |
| `v2/tests/test_daily_fixtures.py` | `enqueue_post(conn, conn, ...)` (×2) | Old single-conn signature |
| `v2/tests/test_daily_quote.py` | `enqueue_post(conn, conn, ...)` (×3) | Old single-conn signature |

---

## Test Counts

| Suite | Before | After | Delta |
|-------|--------|-------|-------|
| v2/tests passed | 1141 | 1167 | +26 (new test_build2_split_ops.py) |
| v2/tests failed | 44 | 44 | 0 |
| v2/tests errors | 73 | 73 | 0 |
| bot/tests passed | 519 | 519 | 0 |
| bot/tests failed | 12 | 12 | 0 |
| bot/tests skipped | 8 | 8 | 0 |
| judging tests | 99/99 | 99/99 | 0 |

**Regression verification:** `diff baseline_v2_failures.txt after_v2_failures.txt` → empty (zero diff). Same for bot tests. The exact same set of 44 v2 failures and 12 bot failures appear before and after.

**Pre-existing failures remain unchanged** (not caused by Build 2):
- `v2/tests/test_reranker_integration.py` — live DB dependency
- `v2/tests/test_rerank_gold_chunks.py` (73 errors) — live DB dependency
- `v2/tests/test_local_server.py` (4 tests, 403) — CORS/host-header issues
- `v2/tests/test_router_precision.py` (9 tests) — router pre-existing regressions
- `v2/tests/test_structured_profiles.py` (1) — surname resolution
- `v2/tests/test_departments.py` (1) — flagged test
- `bot/tests/test_worldcup.py` (8) — asyncio event loop, tests old v1 WorldCupTracker
- `bot/tests/test_router.py` (1), `test_router_v21_flags.py` (1), `test_control_api.py` (1) — pre-existing

---

## Notes for Phase 3 (Event Derive / Cross-DB Writes)

1. **`resolve_org` is in `v2/core/publishing/org_resolve.py`** — import directly, do not redefine.

2. **Connection wiring in `_create_event` (local_server.py):** Phase 3 adds a cross-DB write where the OPS event is committed first, then `derive_event_kb(ops_conn, kb_conn, ...)` writes the `knowledge_item` on kb_conn. Use `get_ops_connection(OPS_PATH)` for OPS and `get_connection(KB_PATH)` for KB. The OPS-commit-first ordering (MED-9) must be preserved.

3. **Two-DB fixture:** extend `two_db` with an event insert into `ops_conn` for derive tests. The `kb_path` and `ops_path` keys are the file paths; both connections are already open.

4. **`org_slug` is now always explicitly set** by `enqueue_post` — no DEFAULT 'gsa' reliance. Phase 3's `derive_event_kb` should do the same for `knowledge_item.metadata.org_slug`.

5. **`MatchWatcher._kb_conn`** is available as a live Knowledge connection during the watcher's run — Phase 3 can use it if the watcher needs to trigger derive (not recommended; derive belongs in local_server flow).

6. **`auto_delete_hours(kb_conn, org_id)`** — the function signature is unchanged; it now explicitly receives the KB conn in production callers.

---

## Goals Checklist (Build 2 scope)

- [x] G4 (partial): all publishing/judging/WorldCup touch points repointed to two-conn model
- [x] G2 (partial): `org_slug` explicitly resolved and stored; `resolve_org` enforces uniqueness
- [x] G6 (behavioral): combined-file mode leaves all tests unchanged (behavior-preserving net proven)
- [ ] G1: OPS DB physically separate — Phase 5 (migration + drop)
- [ ] G3: EVENT→KB derive — Phase 3
- [ ] G4 (dashboard): `/db-ops` + app.js two-DB load — Phase 4
- [ ] G5: gated migration — Phase 5

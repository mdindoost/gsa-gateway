# Build 4 Report — Dashboard /db-ops + app.js Two-DB Load

**Branch:** `worktree-split-ops-db`  
**Commit:** `037fc39`  
**Date:** 2026-06-28  
**Status:** COMPLETE — all tasks shipped, 5 new tests pass, 0 net-new failures, judging 99/99.

---

## What Was Built

### Task 1 — `/db-ops` server endpoint (`v2/local_server.py`)

Added `_send_db_ops_snapshot()` at line ~900 (immediately after `_send_db_snapshot`). It is an exact
mirror of `_send_db_snapshot` with `DB_PATH` replaced by `OPS_DB_PATH`:

- Opens a WAL-consistent backup of `OPS_DB_PATH` into a temp file.
- Streams it as `application/octet-stream` with the same `_cors()` headers as `/db`.
- Routed via `if path == "/db-ops": return self._send_db_ops_snapshot()` immediately after the
  existing `if path == "/db"` line at ~:192.

### Task 2 — `dbOps` global + helpers + three server-load sites (`dashboard/app.js`)

**Global:** Added `let dbOps = null;` on line 8, next to `let db = null;`.

**`queryOps` / `oneOps` / `scalarOps` helpers** (added after the existing `query`/`one`/`scalar` block):
- Mirror the KB helpers exactly, operating on `dbOps` instead of `db`.
- **File-mode safety:** all three return empty array / null when `dbOps` is null, so the dashboard
  degrades gracefully when loaded from a single uploaded file (no `/db-ops` source available).

**Three server-load sites patched:**
1. `reloadFromServer()` — after building `db`, chains `fetch(SERVER_URL + "/db-ops")` → sets `dbOps`.
   Tab re-render moved into the chained `.then()` so it fires after both DBs are loaded.
2. `connectToServer()` — after building `db`, chains `fetch(base + "/db-ops")` → sets `dbOps`.
   `onDbLoaded`, status-label, and db-name updates moved to the chained `.then()`.
3. `reloadDbQuietly()` — after building `db`, chains `fetch(SERVER_URL + "/db-ops")` → sets `dbOps`.
   Errors on the OPS fetch are caught silently (sets `dbOps = null`) so quiet reload never fails.

**`PL.prepareForDashboard` on `dbOps`:** NOT called and correctly so. That helper drops the
`knowledge_fts` sync triggers (FTS5 insert/update/delete triggers on `knowledge_items`). The OPS DB
has no FTS tables at all — calling `prepareForDashboard` on it would throw (no `knowledge_fts` to drop).
Confirmed in the schema: `knowledge_fts` is created only by `create_knowledge_schema`.

### Task 3 — OPS read sites in Overview / Posts (`dashboard/app.js`)

Repointed to `*Ops` helpers:

| Function | Old | New |
|---|---|---|
| `renderOverview` `:868` `FROM events` | `scalar(…)` | `scalarOps(…)` |
| `renderOverview` `:873` `MAX(sent_at) FROM posts` | `scalar(…)` | `scalarOps(…)` |
| `renderOverview` `:874` `MIN(scheduled_for) FROM posts` | `scalar(…)` | `scalarOps(…)` |
| `renderOverview` `:876-877` `FROM posts … LIMIT 10` (recent) | `query(…)` | `queryOps(…)` |
| `renderOverview` `:878-880` `FROM posts WHERE status='scheduled'` (upcoming) | `query(…)` | `queryOps(…)` |
| `openPost` `:958` `FROM posts WHERE id=?` | `one(…)` | `oneOps(…)` |
| `openPost` `:960-961` `FROM post_deliveries WHERE post_id=?` | `query(…)` | `queryOps(…)` |
| `renderPostsList` `:1053` `COUNT(*) FROM posts` | `scalar(…)` | `scalarOps(…)` |
| `renderPostsList` `:1057-1059` posts list | `query(…)` | `queryOps(…)` |
| `renderPostDetail` `:1100` `FROM posts WHERE id=?` | `one(…)` | `oneOps(…)` |
| `renderPostDetail` `:1102` `FROM post_deliveries WHERE post_id=?` | `query(…)` | `queryOps(…)` |

**`renderPostDetail` KB reads kept on `db`:**
- `PL.renderSignature(db, p.org_id, p.signature)` — reads `settings` from KB.
- `scalar("SELECT name FROM organizations WHERE id=?", [p.org_id])` — reads KB org.
These are two separate SQL statements, not a JOIN — split is clean and correct.

### Task 4 — Analytics OPS reads (`dashboard/app.js`)

Repointed to `*Ops` helpers:

| Line | Old | New |
|---|---|---|
| `:1900` `COUNT(*) FROM posts WHERE sent_at IS NOT NULL …` | `scalar(…)` | `scalarOps(…)` |
| `:1901` `COUNT(*) FROM post_deliveries WHERE status='success' …` | `scalar(…)` | `scalarOps(…)` |
| `:1902` `COUNT(*) FROM post_deliveries WHERE status='failed' …` | `scalar(…)` | `scalarOps(…)` |
| `:1912-1915` `FROM posts p LEFT JOIN post_deliveries pd …` | `query(…)` | `queryOps(…)` |

The `posts ⨝ post_deliveries` JOIN in `postsByType` is within OPS (both tables are in OPS DB) — it
runs on `dbOps` which holds both, so no cross-DB JOIN issue.

Questions, feedback, and KB stats (`knowledge_items`, `response_feedback`, etc.) remain on `db`.

---

## File-Mode Write Limitation (Pre-Existing, Not Fixed Here)

In file mode (single uploaded `.db`), `dbOps` is null. OPS reads degrade safely (return empty via the
`*Ops` helper guards). However, the `applyAndExport(UPDATE posts…)` write sites at lines `:995`,
`:1137`, `:1139` call `db.exec(patch)` against `db`, which no longer contains `posts` after the
split. These would throw in file mode when a user tries to cancel/resend a post via the SQL patch path.

**Server mode (the real deployment via SSH tunnel to `:5555`) is unaffected** — writes POST to live
endpoints that route to the OPS connection server-side (Build 2). File-mode post-editing is a
pre-existing degraded path; expanding scope to fix it is deferred.

---

## New Test File

**`v2/tests/test_build4_db_ops.py`** — 5 tests:

1. `test_db_ops_endpoint_returns_sqlite_header` — `/db-ops` returns bytes with SQLite magic header.
2. `test_db_ops_endpoint_contains_posts_table` — `/db-ops` snapshot contains `posts`.
3. `test_db_ops_endpoint_does_not_contain_knowledge_items` — `/db-ops` does NOT have `knowledge_items`.
4. `test_db_endpoint_contains_knowledge_items` — `/db` snapshot contains `knowledge_items`.
5. `test_db_endpoint_does_not_contain_posts` — `/db` snapshot does NOT have `posts`.

**Test-harness approach:** Uses a fresh `db_ops_server` fixture that spins up a real `GatewayHandler`
on a random port, monkeypatches `ls.DB_PATH`, `ls.OPS_DB_PATH`, and `ls.ALLOWED_HOSTS` (adds
`127.0.0.1:<port>` to bypass the DNS-rebinding host-guard), and creates real KB/OPS temp DBs via
`create_knowledge_schema` / `create_ops_schema`. Does NOT reuse the broken `server` fixture in
`test_local_server.py` (which has 6 pre-existing 403 failures due to random-port host mismatch).

---

## Verification Evidence

### New test suite
```
5 passed in 3.53s
```

### Net-new failure diff (baseline 117 lines vs after 117 lines)
```
(empty — diff produced no output)
```
Zero net-new failures. The 117 pre-existing FAILED/ERROR entries are unchanged.

### Judging suite
```
99 passed in 5.19s
```

### JavaScript syntax check
```
node --check dashboard/app.js → SYNTAX OK
```

---

## Manual Smoke Checklist (Server Mode)

Since there is no JS test harness, the following steps would be run manually with the server active
(SSH tunnel to `:5555`):

1. **Overview tab:** Confirm "Active Events", "Recent Posts", "Upcoming Scheduled", "Last post sent",
   "Next scheduled" render from OPS data. Confirm "Knowledge Items", "Embeddings", "Organizations"
   render from KB data.
2. **Posts tab:** Confirm posts list populates; pager shows correct count; clicking a post opens the
   detail pane with deliveries table.
3. **Post detail:** Confirm org name + signature render (from KB); deliveries table renders (from OPS).
4. **Post drill-down modal:** Confirm opening a post from Overview shows correct deliveries.
5. **Analytics tab:** Confirm "Posts Sent", "Deliveries OK/Failed", "Posts by type" chart render.
   Confirm "Questions", "Feedback", "Knowledge Base" sections still render from KB.
6. **KB / People / Settings tabs:** Confirm unchanged (read `db`).
7. **Judging tab:** Confirm unchanged (uses live `/judging/*` API endpoints, not sql.js at all).
8. **Server-mode create + cancel post:** Confirm creating a post POSTs to the live endpoint (not
   `db.exec`); cancelling a scheduled post DELETEs via the live endpoint.
9. **Console check:** `dbOps.exec("SELECT COUNT(*) FROM posts")` returns a result; no console errors.

Note: browser smoke was not run in this session (no browser environment). The above is the exact
checklist for the human gate review.

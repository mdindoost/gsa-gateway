# Split-Ops Build 4 — Dashboard /db-ops + app.js Two-DB Load (SKELETON)

> **SKELETON.** REQUIRED SUB-SKILL: superpowers:test-driven-development (+ manual smoke for the browser).
> **Spec:** Touch Points (dashboard rows), MED-10. **Phase 4 of 5.**

**Goal:** The dashboard reads OPS data (posts, post_deliveries) from a new `/db-ops` snapshot via a
second sql.js database, while KB/People/Settings keep reading `/db`. Judging already uses live
`/judging/*` APIs → server-side repoint only (Phase 2 handled its connection).

**Architecture:** `local_server` serves the OPS DB at `/db-ops` (mirror of `_send_db_snapshot`).
`app.js` loads a second handle `dbOps` alongside `db`; Overview/Posts/Analytics post-queries run on
`dbOps`. Writes still go through live POST endpoints (server-side, correct DB) — no change to the
CSRF write path.

## Global Constraints
- Read-path only for sql.js; all writes stay on the live POST endpoints.
- `dbOps` is loaded/refreshed wherever `db` is (initial load + `reloadDbQuietly`).
- No new pip deps / JS deps (reuse the existing sql.js bootstrap). No Claude/AI attribution.
- L2 report → `build-4-report.md` only.

## File Structure (Modify)
- `v2/local_server.py` — add `/db-ops` route (near `path == "/db"` at `:180`) + `_send_db_ops_snapshot()` (mirror `_send_db_snapshot` at `:871`, pointing at `OPS_DB_PATH`).
- `dashboard/app.js` — add a `dbOps` global; load it from `/db-ops` in the initial bootstrap (`:152`/`:165`) and in `reloadDbQuietly` (`:801`); route post/delivery queries in `renderOverview` (`:862-879`), `renderPosts`, the post drill-down modal (`:958-961`), and `renderAnalytics` post-join (`:1885+`) to `dbOps`.

## Tasks (skeleton)
### Task 1 — `/db-ops` endpoint (server)
- Test: GET `/db-ops` returns the OPS DB bytes (a valid SQLite header); `/db` still returns the KB DB; both are consistent snapshots. (Python test against `GatewayHandler` with temp DBs.)

### Task 2 — app.js loads `dbOps`
- Smoke: dashboard boots with both `db` and `dbOps` populated; `reloadDbQuietly` refreshes both. (Add a tiny JS guard/log; verify in browser.)

### Task 3 — Overview/Posts read `dbOps`
- Smoke: Overview "last sent / next scheduled / recent / upcoming" render from `dbOps`; Posts tab list + drill-down (post_deliveries) render from `dbOps`. KB/People/Settings unaffected (still `db`).

### Task 4 — Analytics split-DB
- Smoke: `renderAnalytics` reads `questions`/`response_feedback`/`knowledge_items`/`organizations` from `db` and the `posts ⨝ post_deliveries` block from `dbOps`. No cross-DB JOIN (there is none today — verified).

## Acceptance
- Manual smoke checklist in the report: Overview, Posts (+ drill-down), Analytics all render correctly
  with posts coming from `/db-ops`; KB/People/Settings/Judging unchanged. Server-mode writes (create/
  cancel post) still work through the live endpoints.

## Report → `build-4-report.md`: the `/db-ops` route + which app.js functions were repointed; the smoke results.

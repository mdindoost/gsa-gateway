# Split-Ops Build 4 — Dashboard /db-ops + app.js Two-DB Load (FINAL)

> **FINAL** (locked against Build 1/2/3 reports + live code at branch tip `22aab66`).
> REQUIRED SUB-SKILL: superpowers:test-driven-development (+ manual smoke for the browser).
> **Spec:** `docs/superpowers/specs/2026-06-28-split-ops-db-design.md` (Touch Points → dashboard rows, MED-10). **Phase 4 of 5.**

**Goal:** The dashboard reads OPS data (`posts`, `post_deliveries`, `events`) from a new `/db-ops`
snapshot via a *second* sql.js database (`dbOps`), while KB/People/Settings/Analytics-KB keep reading
`/db`. Judging already uses the live `/judging/*` APIs (Build 2 repointed those server-side) → no app.js
change for judging.

**Architecture:** `local_server` serves the OPS DB at `/db-ops` (mirror of `_send_db_snapshot`).
`app.js` loads a second handle `dbOps` everywhere it loads `db`; the post/delivery/event *read*
queries run on `dbOps` via new `queryOps`/`oneOps`/`scalarOps` helpers. Writes are UNCHANGED — in
server mode they POST to the live endpoints (Build 2 already routes them to the OPS connection
server-side).

## Why a second sql.js DB (not a JOIN)
SQLite cannot JOIN across two files in the browser. **Verified there is NO cross-DB JOIN today:**
every query that touches `posts`/`post_deliveries`/`events` touches ONLY OPS tables, EXCEPT
`renderPostDetail` which does TWO SEPARATE queries — post+deliveries (OPS) and an org-name lookup
`SELECT name FROM organizations WHERE id=?` + `PL.renderSignature(db, p.org_id, …)` (KB). Those stay
on `db` (separate statements, not a JOIN) — the post row still carries `org_id` (informational, kept
by Build 1/2). So the split is clean: OPS reads → `dbOps`, KB reads → `db`.

## Global Constraints
- **Read-path only** for sql.js; all writes stay on the live POST endpoints (server mode).
- `dbOps` is loaded/refreshed **wherever `db` is** — there are THREE server-load sites (see below) +
  the file-mode loader (file mode has no OPS file → `dbOps` stays null; OPS reads must degrade safely).
- No new pip/JS deps (reuse the existing sql.js bootstrap). No Claude/AI attribution in code/commits.
- L2 report → `build-4-report.md` ONLY. Do NOT edit BUILD_LEDGER.md or any memory file (orchestrator-owned).

## Verified anchors (branch tip 22aab66 — re-confirm before editing; line numbers drift)
### `v2/local_server.py`
- `OPS_DB_PATH` already defined at **:35** (`OPERATIONS_DB_PATH` env, default `gsa_gateway_ops.db`).
- `_ops_conn()` at **:106**; `do_GET` at **:166**; `if path == "/db": return self._send_db_snapshot()` at **:191**.
- `_send_db_snapshot()` at **:882** — copies `DB_PATH` via `sqlite3 .backup` to a temp file, streams bytes.

### `dashboard/app.js`
- Globals: `let db = null;` at **:7**.
- `query/one/scalar` helpers at **:181-193** (all use global `db`).
- Server DB-load sites (ALL must also load `dbOps`):
  - `reloadFromServer()` **:151** (`fetch(SERVER_URL + "/db")`)
  - `connectToServer()` **:165** (initial bootstrap path: health → `/db`)
  - `reloadDbQuietly()` **:801** (post-job quiet refresh)
- File-mode loader `loadDatabaseBytes()` **:39** (single uploaded file → no OPS; leave `dbOps` null).
- OPS-table read sites to repoint to the `*Ops` helpers:
  - `renderOverview` **:868** `FROM events`, **:873-879** posts (lastSent/nextSched/recent/upcoming)
  - post drill-down modal **:958** `FROM posts`, **:961** `FROM post_deliveries`
  - `renderPostsList` **:1053** `COUNT(*) FROM posts`, **:1058** posts list
  - `renderPostDetail` **:1100** `FROM posts`, **:1102** `FROM post_deliveries` (KEEP org-name `:` + `renderSignature` on `db`)
  - `renderAnalytics` **:1900-1902** posts/post_deliveries counts, **:1912-1915** `posts ⨝ post_deliveries` (within OPS — fine)
- OPS-table WRITE sites (DO NOT repoint reads here; see file-mode caveat): `applyAndExport(UPDATE posts …)` at **:995, :1137, :1139**.

## File Structure (Modify)
- `v2/local_server.py` — add `/db-ops` route (next to `path == "/db"` at :191) + `_send_db_ops_snapshot()`
  (mirror `_send_db_snapshot`, source = `OPS_DB_PATH`).
- `dashboard/app.js` — add `dbOps` global (next to `db` at :7); add `queryOps/oneOps/scalarOps` (mirror
  :181-193 against `dbOps`, returning empty/null when `dbOps` is null); load `dbOps` from `/db-ops` in the
  three server-load sites; repoint the OPS read sites listed above to the `*Ops` helpers.

## Tasks (TDD)
### Test-harness gotchas (READ before writing Task 1)
- The existing `server` fixture in `test_local_server.py` is **broken on base** (6 pre-existing 403
  failures): `do_GET`'s `_host_ok()` guard (`:147`) only allows Hosts in `ALLOWED_HOSTS` (`:55`), which
  is keyed to the configured `PORT` (5555) + bare `localhost`/`127.0.0.1` — but the fixture binds a
  RANDOM port, so the `Host: 127.0.0.1:<rand>` header is rejected (the harness predates the guard).
  These 403s are PRE-EXISTING, NOT net-new. **Do not "fix" them.**
- For the new `/db-ops` test, write a fresh fixture (do NOT reuse the broken one) that monkeypatches
  BOTH `ls.DB_PATH` (KB temp db) AND `ls.OPS_DB_PATH` (OPS temp db), AND `ls.ALLOWED_HOSTS` to include
  the actual bound host (e.g. add `f"127.0.0.1:{port}"`), so the request passes `_host_ok()`. Build the
  KB db with `create_knowledge_schema` and the OPS db with `create_ops_schema` (Build-1 seams). Then
  assert `GET /db` has `knowledge_items` (not `posts`) and `GET /db-ops` has `posts` (not `knowledge_items`).
  See `test_build2_split_ops.py` / `test_event_projection.py` for the create_knowledge_schema/create_ops_schema
  + temp-db pattern.

### Task 1 — `/db-ops` endpoint (server) — Python test
- **Test** (`v2/tests/`): construct a `GatewayHandler` (or drive `do_GET`) against temp KB + OPS DBs;
  assert `GET /db-ops` returns bytes with a valid SQLite header (`b"SQLite format 3\x00"`) and that the
  returned snapshot contains the OPS tables (e.g. `posts`) and NOT KB-only tables (e.g. `knowledge_items`),
  while `GET /db` returns the KB snapshot (has `knowledge_items`, not `posts`). Mirror the existing
  `_send_db_snapshot` test pattern if one exists; otherwise follow the Build-1/2 GatewayHandler test style.
- **Impl**: `_send_db_ops_snapshot()` = copy of `_send_db_snapshot` with `DB_PATH` → `OPS_DB_PATH`; route
  `if path == "/db-ops": return self._send_db_ops_snapshot()`.

### Task 2 — app.js loads `dbOps` (manual smoke, no JS test harness exists)
- Add `let dbOps = null;` and `queryOps/oneOps/scalarOps`. In each of the three server-load sites, after
  building `db`, also `fetch(base + "/db-ops") → new SQL.Database(...)` into `dbOps`. Do NOT call
  `PL.prepareForDashboard(dbOps)` unless PostsLogic needs it for OPS tables — check: that helper drops
  knowledge_fts triggers (KB-only); OPS has no FTS → safe to SKIP for dbOps. Confirm and note in report.
- **Smoke**: with the server running, boot the dashboard; in console confirm both `db` and `dbOps` are
  populated and `dbOps.exec("SELECT COUNT(*) FROM posts")` works.

### Task 3 — Overview/Posts read `dbOps`
- Repoint the OPS read sites in `renderOverview`, the drill-down modal, `renderPostsList`,
  `renderPostDetail` to `*Ops`. Keep `renderPostDetail`'s org-name + `renderSignature` on `db`.
- **Smoke**: Overview "active events / last sent / next scheduled / recent / upcoming" render; Posts list
  + pager + drill-down (deliveries table) render; KB/People/Settings unaffected.

### Task 4 — Analytics split-DB
- Repoint `renderAnalytics` posts/deliveries queries to `*Ops`; leave questions/feedback/KB on `db`.
- **Smoke**: Analytics renders both blocks; no console error; the `posts ⨝ post_deliveries` chart shows data.

## File-mode caveat (document, do NOT fix here)
In file mode (single uploaded `.db`, no server) `dbOps` is null → OPS reads return empty (helpers guard).
Post WRITE patches (`applyAndExport(UPDATE posts…)` at :995/1137/1139) run `db.exec` against `db`, which
no longer has `posts` → they would throw in file mode. **Server mode (the real deployment, SSH tunnel to
:5555) is unaffected** — writes POST to live endpoints (Build 2, correct OPS conn). File-mode post-editing
is a pre-existing degraded path; note it in the report as a known limitation, do not expand scope.

## Acceptance
- Task-1 Python test passes; full v2 suite shows **ZERO net-new failures** vs the base worktree
  (in-location diff — see orchestrator gate method) and judging 99/99.
- Manual smoke checklist in the report: Overview, Posts (+ drill-down), Analytics render with OPS data
  from `/db-ops`; KB/People/Settings/Judging unchanged; a server-mode create + cancel post still works
  through the live endpoints.

## Report → `build-4-report.md`
The `/db-ops` route + `_send_db_ops_snapshot`; the `dbOps` helpers + the three load sites touched; which
app.js read sites were repointed; whether `prepareForDashboard` was applied to `dbOps` (and why); the
smoke results; the file-mode write limitation; the net-new-failure diff evidence.

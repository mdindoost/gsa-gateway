# Split-Ops Build 2 — Repoint Subsystems to Two Connections (SKELETON)

> **SKELETON.** Structural plan; finalize the exact signatures marked «LOCK AFTER P1» against Build-1's
> `build-1-report.md` before dispatch. REQUIRED SUB-SKILL: superpowers:test-driven-development.
> **Spec:** `2026-06-28-split-ops-db-design.md` §3.2, §3.4, Touch Points. **Phase 2 of 5.**

**Goal:** Make every publishing/judging code path use the OPS connection for posts/deliveries/events/
judging and the KNOWLEDGE connection for `organizations`/`settings`, with org resolved by slug (cached
per tick). No data move (that's Phase 5); behavior must be unchanged when both DBs point at the same file.

**Architecture:** Introduce a two-connection seam through the scheduler stack, `enqueue_post`, the
WorldCup watcher, and judging. **LOCKED:** Build 2 CREATES `v2/core/publishing/org_resolve.py` with
`resolve_org(kb_conn, slug) -> sqlite3.Row` (returns the org row incl `id`; raises on unknown / on >1
match per LOW-11) + a per-tick cache helper. Phase 3's `event_projection.py` REUSES this module (does
not define its own). Build-1 seams available (from `build-1-report.md`): `create_knowledge_schema` /
`create_ops_schema` / `get_ops_connection`; OPS `posts`/`events`/`post_templates` already carry
`org_slug` (currently `DEFAULT 'gsa'` — Build 2 sets it EXPLICITLY in `enqueue_post`, removing reliance
on the default). The events-STRICT test was already updated in Build 1 (no action here).

## Global Constraints
- No data move/drop. No live-DB writes. No new pip deps. No Claude/AI attribution.
- **Behavior-preserving:** with `ops_path == kb_path` (one combined file via `create_all`), all existing
  publishing + judging tests must pass unchanged. This is the safety net for "no behavior change".
- Settings/org reads → KNOWLEDGE conn; posts/post_deliveries/events/event_reminders/post_templates/
  judging writes+reads → OPS conn. `org_slug` is the stored contract; settings reads use the resolved
  `id` from `resolve_org` (per-tick cache), NOT a stored cross-DB rowid.
- L2 report → `docs/superpowers/plans/split-ops/build-2-report.md` only. Never touch memory or the ledger.

## File Structure (Modify)
- `v2/integration/scheduler_runner.py` — open BOTH conns; thread to publisher/scheduler/deleter/registry/signature.
- `v2/core/publishing/publisher.py` — `PostPublisher(ops_conn, kb_conn, registry, signatures)`; posts on ops, settings on kb.
- `v2/core/publishing/signature.py` — `SignatureService(kb_conn)`.
- `v2/core/connectors/registry.py` — `registry.conn` = OPS (writes `post_deliveries`).
- `v2/core/publishing/deleter.py` — posts/deliveries on OPS.
- `v2/core/publishing/sources.py` — `enqueue_post(ops_conn, kb_conn, draft, …)`; `auto_delete_hours(kb_conn, org_id)`; `SourceRunner(ops_conn, kb_conn, …)`; set `org_slug` on insert.
- `v2/core/publishing/scheduler.py` — `Scheduler(ops_conn, kb_conn, …)`; `materialize_templates`/`materialize_event_reminders`/`publish_due` use ops for cluster, kb for org/settings.
- `v2/integration/match_watcher.py` — open both; org-resolve + `auto_delete_hours` on kb; enqueue on ops. `EspnMatchWatcher` inherits.
- `v2/core/judging/db.py`, `session.py` — `JudgingSessionManager(ops_path)`; `_conn()` connects to ops.
- `bot/main.py` — pass `(operations_db_path, database_path)` to SchedulerRunner + watcher (replace hardcoded `"gsa_gateway.db"` at `:236`); judging gets `operations_db_path`.

## Tasks (skeleton — each is full TDD: failing test → impl → green → commit)
### Task 1 — `resolve_org` + per-tick cache (NEW module `v2/core/publishing/org_resolve.py`)
- Test: `resolve_org(kb_conn, "gsa")` returns row with `id`; unknown slug → raises `ValueError`; >1 match → raises (LOW-11). Cache returns same row within a tick, refreshes across ticks.
- Impl: create `org_resolve.py`. `resolve_org(kb_conn, slug)` = `SELECT * FROM organizations WHERE slug=?`; assert exactly one row. Provide a small `OrgCache`/`resolve_cached(kb_conn, slug, cache)` the scheduler clears each tick.

### Task 2 — Publisher reads settings on kb, posts on ops
- Test: a post row in OPS (with org_id+org_slug) publishes; `_platforms/_discord_channel/_telegram_channel/_groupme_group/signatures` read from the KB settings; status lifecycle writes go to OPS. Use a two-DB fixture.
- Impl: `PostPublisher.__init__(ops_conn, kb_conn, registry, signatures)`; replace `self.conn` post ops → `ops_conn`; settings helpers take `kb_conn` + resolved id.

### Task 3 — SignatureService(kb_conn) + registry.conn=OPS
- Test: signature renders from KB settings; delivery rows land in OPS `post_deliveries`.

### Task 4 — `enqueue_post` two-conn + org_slug
- Test: enqueue validates `organizations.is_active` on kb_conn; inserts post into ops_conn WITH `org_slug` resolved from kb; dedup query hits ops; raises on inactive/unknown org. `auto_delete_hours(kb_conn, org_id)`.
- Impl: `enqueue_post(ops_conn, kb_conn, draft, *, allowed_channels=None)`; back-fill `org_slug` from `resolve_org`.

### Task 5 — Scheduler two-conn (templates/reminders/publish_due)
- Test: `materialize_templates` reads `post_templates` (ops, w/ org_slug) → emits posts (ops); `materialize_event_reminders` joins events+reminders (ops) → posts (ops); org/settings stamping via kb.

### Task 6 — WorldCup watcher two-conn
- Test: watcher resolves org by slug on kb; `auto_delete_hours` on kb; `enqueue_post` writes to ops. `EspnMatchWatcher` subclass still works. bot/main wiring passes both paths.

### Task 7 — Judging repoint
- Test: `JudgingSessionManager(ops_path)` runs the full judging flow against an OPS-only DB; the 49 judging tests pass against ops path. (Self-contained — no org/settings dependency.)

### Task 8 — scheduler_runner wires both + integration smoke
- Test: `SchedulerRunner(ops_path, kb_path, registry)` opens both; an end-to-end tick (enqueue → publish_due → deliver → delete_due) works on a two-DB fixture; AND works when ops_path==kb_path (behavior-preserving net).

## Acceptance
- Two-DB fixture: enqueue→publish→deliver→delete + full judging flow green.
- Combined-file mode (ops==kb) green for ALL pre-existing publishing+judging tests (no behavior change).
- Grep proof: no remaining `posts`/`post_deliveries`/`events`/`event_reminders`/`post_templates`/`judging_*`
  access on a KNOWLEDGE-only connection (other than `organizations`/`settings`).

## Report → `build-2-report.md`: final two-conn signatures (Phase 3/5 need them), the resolve_org location, any behavior-preserving test that needed a fixture change, test counts.

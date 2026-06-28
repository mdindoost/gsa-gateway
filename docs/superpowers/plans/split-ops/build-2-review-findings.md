# Build 2 — Consolidated Dual-Review Findings (Claude + Codex) → FIX SET

**Verdict (both reviewers, independently): CHANGES-REQUIRED.** Core scheduler/publisher/enqueue/
match_watcher repoint is CORRECT (verified). But the repoint is incomplete on several callers the
existing tests don't cover, and the slug-resolve mechanism is unwired. All items below were
re-verified against the code by the orchestrator. Codex tree-write check: clean (no modifications).

## MUST FIX (functional crash / data-integrity / latent cutover break)

**F1 (HIGH) — `migrate_events_columns()` deleted in Build 1 but still called by 5 entrypoints → crash.**
`run_telegram.py:43`, `run_groupme.py:50`, `scripts/eval_run.py:68`, `scripts/_eval_kb_100.py:146`,
`scripts/trace_query.py:119`. `scripts/restart.sh` launches run_telegram + run_groupme → next restart
crashes BOTH bots with `AttributeError`. (Build-1 leftover; not covered by tests.)
FIX: remove the 5 calls. Keep a one-line no-op `migrate_events_columns()` stub on `Database` (deprecation
comment) as belt-and-suspenders for any external caller.

**F2 (HIGH) — Judging NOT repointed at any caller (runs on KB).**
`run_telegram.py:60` `JudgingSessionManager(db_path=config.database_path)` → KB.
`v2/local_server.py:97-98` `_conn()` → `sqlite3.connect(DB_PATH)` (KB); every `jdb.*` judging endpoint
runs on KB. Works only because `judging_*` still physically lives in KB; **breaks after Build-5 drops it.**
FIX: `run_telegram.py` (+ `run_groupme.py` if it wires judging) pass `config.operations_db_path` to
`JudgingSessionManager`. In `local_server.py` add an OPS connection (`OPS_DB_PATH` already defined at :35)
and route the judging endpoints' `jdb.*` calls through it. (The /db-ops sql.js snapshot stays Build 4;
this is the server-side LIVE judging API repoint, which is Build 2's job.)

**F3 (MED) — failure-digest `SourceRunner` on the OLD single-conn signature.**
`bot/main.py:307` `get_connection("gsa_gateway.db")` (hardcoded KB) + `:320` `SourceRunner(conn, source, ...)`
→ wrong args (silently swallowed by `except` → digest never starts). The fixtures digest right above was
fixed; this block was missed.
FIX: mirror the fixtures digest — `kb=get_connection(config.database_path)` for `FailureDigestSource` +
org lookup; `ops=get_ops_connection(config.operations_db_path)`; `SourceRunner(ops, kb, source, interval=3600)`.

**F4 (MED) — materializers insert posts WITHOUT `org_slug` (rely on schema DEFAULT 'gsa').**
`v2/core/publishing/scheduler.py:169` (templates) + `:201` (reminders). Non-GSA → post with `org_id`≠gsa
but `org_slug='gsa'` (contract-integrity bug, masked because all live data is gsa).
FIX: stamp `t["org_slug"]` in `materialize_templates`; add `e.org_slug` to the reminder join + stamp it.
Add `org_slug` assertions to the materializer tests.

**F5 (MED) — bot v1 `events` CRUD reads/writes KB on a LIVE food path.**
`bot/services/database.py` `add_event`/`get_upcoming_events_db`/`get_all_events`/`mark_*` use `self.conn`
(KB). LIVE path: `message_handler.py:602` → `food_detector.get_food_events(db=…)` → `get_upcoming_events_db()`.
Breaks after Build-5 drops `events` from KB. `bot/tests/conftest.py` was patched to re-create OPS_EVENTS on
the KB conn — which MASKS the gap rather than exercising the split.
FIX: give `Database` an OPS connection (`config.operations_db_path`) and route the 4 events methods to it;
update `conftest.py` to use a real ops conn for events (remove the masking bridge). Verify the food path.

## SHOULD FIX (contract + dead code)

**F6 (LOW) — `resolve_org`/`OrgCache` are DEAD CODE; slug-resolve unwired; LOW-11 not enforced on live paths.**
Publisher reads settings by `row["org_id"]` (publisher.py:65+); enqueue by org_id; no tick clears a cache;
`match_watcher.start()` + `bot/main.py` use raw `SELECT … WHERE slug=? fetchone()` (silently picks first on
>1 match). Spec §3.2/MED-7 require resolving via slug.
FIX: wire `resolve_org`(+per-tick `OrgCache`, cleared at the top of `Scheduler.tick`) into the publisher's
settings reads (resolve `row["org_slug"]`→id) and replace the raw slug `fetchone()` sites (match_watcher,
bot/main fixtures + failure digest) with `resolve_org` (fail-loud on >1). Removes dead code, enforces LOW-11.

**F7 (LOW) — `match_watcher.start()` leaks the OPS conn if the KB open raises** (both opens outside `try`).
FIX: move both `get_*_connection` opens inside the `try`/guard cleanup of both.

**F8 (LOW) — weak watcher test.** `test_match_watcher_start_resolves_org_from_kb` never calls `start()`;
manually assigns `_ops_conn` (not a production attr). FIX: exercise real `await start()` with a patched
loop/provider + a duplicate-slug assertion (after F6).

## Report-accuracy note
The build-2 report over-claimed "all judging/WorldCup touch points repointed" and "resolve_org per-tick
cache enforces uniqueness." Neither was true. The fix agent's report must be accurate.

## Acceptance for the fix
- All 5 `migrate_events_columns` callers fixed; restart of telegram/groupme entrypoints does not crash
  (import/AttributeError check).
- Judging runs on OPS at every caller (run_telegram + local_server endpoints); a test exercises a wired caller.
- failure-digest SourceRunner uses two-conn signature.
- materializers stamp the real `org_slug` (asserted in tests).
- bot events CRUD reads/writes OPS; food path verified; conftest no longer masks the split.
- resolve_org/OrgCache wired (no dead code); raw slug fetchone sites replaced; per-tick cache cleared.
- Re-run: ZERO net-new failures vs base (in-location diff); judging 99/99.

## RESOLUTION (fix agent commit 6563686 + orchestrator commit 4ffb064) — GATE CLEARED
F1–F5, F7, F8 FULLY FIXED + orchestrator-verified (no migrate_events_columns calls; judging on OPS at
run_telegram + local_server `_ops_conn`; failure-digest two-conn; materializers stamp org_slug; bot events
CRUD on OPS conn + conftest de-masked; watcher leak fixed; watcher test exercises real start()).
**F6 — resolve_org genuinely wired** (match_watcher, bot/main digests; fail-loud on >1 slug = LOW-11).
The fix agent left a COSMETIC per-tick OrgCache in `Scheduler.tick` (created+cleared, never `.get()`) because
the publisher (its intended consumer) still reads settings by `row["org_id"]`. Orchestrator REMOVED the
cosmetic cache (commit 4ffb064) and DEFERRED the publisher/signature slug-resolve to the **DB-wipe+rebuild
project** (its correct home — org_id only renumbers there; reviewer-sanctioned: Claude LOW-6 = "acceptable as
deferral"; converting now would break publisher tests that don't seed a resolvable org, for zero split-ops
correctness gain — posts.org_id stays valid through this migration). The OrgCache class stays as a tested
utility for that future conversion. Verified: ZERO net-new failures (v2+bot, in-location), judging 99/99.

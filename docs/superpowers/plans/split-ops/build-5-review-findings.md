# Build 5 — Consolidated Dual-Review Findings (Claude SE + Codex) + Orchestrator

**Verdict (both reviewers): CHANGES-REQUIRED.** Both AGREE the core drop-LAST control flow is
immortal-safe by construction (every early-exit returns before any drop; drop is the last step after a
fully-passed gate; column-mapping + checksum + sqlite_sequence + FK-ordered drop are correct). The gaps
are the cutover runbook, idempotency, the natural_key backfill source, the slug gate, and test/rollback
polish. Orchestrator independently verified: script is WAL-aware (reads 1180 vs live), dry-run writes
nothing, 41 tests pass, judging 99/99, ZERO net-new failures.

Fold ALL of the following (this is the immortal-data migration — strictness warranted).

## F1 [HIGH] — Cutover runbook: stopping writers is MANDATORY, and the current command is WRONG
`docs/superpowers/plans/split-ops/build-5-report.md:146-147` says stopping services is "optional ... to
avoid WAL contention" and uses `bash scripts/restart.sh --no-llm`. Confirmed (`scripts/restart.sh`):
`--no-llm` stops ONLY Ollama and RESTARTS the Discord/Telegram bot + dashboard — leaving live writers
(WorldCup watcher, scheduler delivery loop, PostDeleter all write posts/post_deliveries) running through
the migration. A row written to KB after the gate passes but before `DROP TABLE` is silently lost.
**Fix:** rewrite the cutover section so step 2 MANDATES a true full stop with NO restart, e.g.
`pkill -TERM -f "bot\.main"; pkill -TERM -f "v2/local_server\.py"` then verify with
`pgrep -af "bot\.main|v2/local_server\.py"` (must be empty). Restart happens ONLY at the final step on the
two-conn code. Frame it as an immortal-data-loss guard, not "contention".

## F2 [HIGH] — Script defense-in-depth for the gate→drop window
`scripts/split_ops_migrate.py:496` plain `sqlite3.connect` (no busy_timeout); drop loop at `:627-633`
has no pre-drop re-verify and no lock handling.
**Fix (all three):**
1. `kb_conn.execute("PRAGMA busy_timeout=5000")` after connect (a stray lock fails cleanly, not mid-drop).
2. IMMEDIATELY before the drop loop, RE-VERIFY each MOVED table's KB count (and checksum) against the
   gate-verified values; if ANY differs, ABORT before dropping anything (collapses the loss window to
   ~zero even if a writer sneaks in). 
3. A loud pre-commit warning/confirmation that all bot/dashboard processes MUST be stopped.
(The script-level guards are belt-and-suspenders; F1's stop-services is the operational guarantee.)

## F3 [HIGH] — natural_key backfill must source name/date/time from the matched OPS event
`scripts/split_ops_migrate.py:399-411` computes the key from KB `event_info.title` + `metadata.date/time`.
`derive_event_kb` (`event_projection.py:112`) computes it from OPS `events.name/date/time or "TBD"`.
Title is fine (derive sets title=events.name), but the **time default diverges**: a legacy row with
`metadata.date` but no `metadata.time` gets `"TBD"` from backfill while derive uses the real
`events.time` → keys differ → derive can't match (MED-8 fallback is gated on natural_key IS NULL, which
backfill just set) → DUPLICATE event_info (violates reject #7). Zero impact on current live data
(event_info=0) but wrong for the rebuild.
**Fix:** backfill the natural_key (and the ki_content match) from the MATCHED OPS `events` row found via
`metadata.ops_event_id`/`event_id` — single source of truth — or leave natural_key absent when there is
no OPS match. Add a regression test with title≠name AND missing `metadata.time` asserting
`derive_event_kb(...)` returns `created == 0`.

## F4 [MED] — Enforce greenfield OPS (assert empty before copy)
`create_ops_schema` is additive; if OPS already holds rows (prior aborted `--commit`, or the two-conn bot
wrote posts before migrating), the copy collides on PK (caught → safe abort) but leaves the operator
stuck. **Fix:** before backup/copy, assert every one of the 11 MOVED tables in OPS has `COUNT(*)==0`;
if not, abort with an explicit message ("OPS already populated — delete gsa_gateway_ops.db and re-run
with services stopped"). Turn a confusing IntegrityError into a clear instruction.

## F5 [MED] — Slug gate must check correctness, not just non-empty
`scripts/split_ops_migrate.py:333` only checks `org_slug IS NULL OR org_slug=''`. Because the checksum
intentionally excludes org_slug, a WRONG non-empty slug passes the gate. **Fix:** assert every OPS
posts/post_templates/events row has `org_slug == org_slug_map[org_id]`, failing on mismatch or missing org_id.

## F6 [LOW] — Reject #7 not gate-enforced (document it)
The acceptance gate covers reject #1/#3/#5/#6 but not #7 (no-duplicate-event_info-on-re-derive); #7 is a
test-level guarantee. With F3's fix + the F3 regression test, #7 holds. Do NOT run derive inside the gate
(it mutates KB). **Fix:** add a one-line note in the gate docstring/report that #7 is enforced by the
backfill-from-OPS fix + the regression test, and is a no-op at event_info=0 on current live data.

## F7 [LOW] — Reversibility test must assert byte/checksum identity, not just counts
`v2/tests/test_split_ops_migrate.py:761-763` checks restored counts only. **Fix:** capture
`table_checksum` of the seeded KB MOVED tables before migration, restore the backup, assert equal.

## F8 [LOW] — main()-level fail-closed tests
Most failure-mode tests call `acceptance_gate()` directly (prove the function, not the orchestrator).
**Fix:** add ≥1 test driving `main(["--commit", ...])` with a forced gate failure (e.g. count/checksum/fk)
and assert ALL 11 KB MOVED tables still exist afterward (orchestrator drops nothing).

## F9 [LOW] — Rollback recipe completeness
`scripts/split_ops_migrate.py:541-542,640-641` + report rollback omit (a) stop services first and (b)
delete the now-populated `gsa_gateway_ops.db` (stale OPS would collide on retry / split-brain). **Fix:**
rollback = stop services → `cp <backup> <kb_path>` → delete `gsa_gateway_ops.db` → restart on pre-split code.

## Verification after fixes
Re-run: new test file green; full v2 suite ZERO net-new vs baseline (117); judging 99/99; re-run the
copy-of-live proof. Then orchestrator re-reviews + (critical phase) a Codex re-check of the diff before
the gate clears.

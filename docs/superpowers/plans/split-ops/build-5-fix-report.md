# Build 5 Fix Report — Dual-Review Findings F1-F9

**Commit:** `588e1d0` on branch `worktree-split-ops-db`
**Date:** 2026-06-28

---

## F1 [HIGH] — Cutover runbook: stopping writers is MANDATORY

**File changed:** `docs/superpowers/plans/split-ops/build-5-report.md`

Step 2 in the runbook previously read "optional but recommended" and used `restart.sh --no-llm` (which RESTARTS the bot while leaving WorldCup watcher + PostDeleter running).

**Fix:** Rewrote step 2 to:
```bash
pkill -TERM -f 'bot\.main'
pkill -TERM -f 'v2/local_server\.py'
pgrep -af 'bot\.main|v2/local_server\.py'   # must be empty
```
Framed as an immortal-data-loss guard (not "contention"), restart moved to final step 5.

---

## F2 [HIGH] — Script defense-in-depth for the gate→drop window

**File changed:** `scripts/split_ops_migrate.py`

Three additions to close the loss window between gate pass and drop:

1. **busy_timeout** (`~line 501`): `kb_conn.execute("PRAGMA busy_timeout=5000")` immediately after connect — a stray lock fails cleanly instead of mid-drop.

2. **Pre-drop re-verify** (before Step 6): Re-reads every MOVED table's count + checksum from both KB and OPS; if any differ from the gate-verified values, aborts with "STOP ALL WRITERS and re-run" before touching any DROP.

3. **Loud warning** (before Step 6): Prints a `!`-bordered banner listing the pkill commands writers must have received before this point.

---

## F3 [HIGH] — natural_key backfill must source from OPS events row

**File changed:** `scripts/split_ops_migrate.py` (function `backfill_event_info_natural_key`)

**Root bug:** old code computed `event_natural_key(title, metadata.date, metadata.time)` from KB fields. A legacy row with `title != OPS event name` OR missing `metadata.time` produced a key that `derive_event_kb` couldn't match → duplicate `event_info` (reject #7 violation).

**Fix:** Changed signature to `backfill_event_info_natural_key(kb_conn, ops_conn)`. For each event_info row, looks up `metadata.ops_event_id`/`event_id` in OPS `events`; uses the OPS row's `name`/`date`/`time` as the single source of truth. If no OPS match → skip (natural_key left absent, MED-8 fallback still fires on next derive).

**Regression test added** (`test_natural_key_back_fill_uses_ops_event_name_not_kb_title`): seeds an event_info with title `"Spring Celebration"` (≠ OPS name `"Spring Social"`) and no `metadata.time`, back-fills, then asserts `derive_event_kb` returns `created == 0`.

**Cascading test updates:** Three existing tests that called `backfill_event_info_natural_key(kb_conn)` were updated to create an OPS DB, seed the matching event, and pass `ops_conn`. One test (`test_derive_event_kb_reproduces_content_byte_identically`) had its call site updated similarly.

---

## F4 [MED] — Greenfield OPS assertion before copy

**File changed:** `scripts/split_ops_migrate.py` (in `main()`, after Step 2)

After building the OPS schema, asserts every MOVED table has `COUNT(*)==0`. If any table has rows (from a prior aborted `--commit`, or the two-conn bot writing to OPS before migration), aborts with:
```
ABORT: OPS already populated — delete gsa_gateway_ops.db and re-run with services stopped.
```
Converts a confusing `IntegrityError` PK collision into a clear recovery instruction.

---

## F5 [MED] — Slug gate checks correctness, not just presence

**File changed:** `scripts/split_ops_migrate.py` (`acceptance_gate`, check 3)

Old gate: `WHERE org_slug IS NULL OR org_slug=''` — a wrong-but-non-empty slug silently passed.

**Fix:** For every row in `posts`/`post_templates`/`events`, asserts `org_slug == org_slug_map[org_id]`. Fails on mismatch, NULL org_id, or org_id not in the map. Reports `wrong_rows` list in the structured check dict.

**New test** (`test_gate_fails_on_wrong_but_non_empty_slug`): copies tables, corrupts one row's `org_slug` to `'wrong-slug'`, asserts gate fails with `posts_slug_resolved` FAIL.

---

## F6 [LOW] — Reject #7 enforcement documented in gate docstring

**File changed:** `scripts/split_ops_migrate.py` (`acceptance_gate` docstring)

Added note: reject #7 (no-duplicate-event_info-on-re-derive) is enforced by the F3 OPS-sourced backfill fix + the F3 regression test. The gate does NOT run `derive_event_kb` (it mutates KB). On current live data (event_info=0) the check is a no-op.

---

## F7 [LOW] — Reversibility test asserts byte/checksum identity

**File changed:** `v2/tests/test_split_ops_migrate.py` (`test_restore_from_backup_reverts_migration`)

**Before:** only checked row counts (2 posts, 3 deliveries, 2 events) after restore.

**Fix:** Captures `table_checksum` for all 11 MOVED tables BEFORE running migration, then after restore asserts each table's checksum equals the pre-migration digest. Any bit-level change in a restored table would now be caught.

---

## F8 [LOW] — main()-level fail-closed test

**File changed:** `v2/tests/test_split_ops_migrate.py` (new test `test_main_commit_forced_gate_fail_all_11_tables_intact` in `TestDropLastAndReversibility`)

Drives `main(["--commit", ...])` via a subprocess wrapper that patches `acceptance_gate` to always return `{"passed": False, "checks": {"posts_count": {"status": "FAIL", ...}}}`. Asserts:
- Exit code is nonzero
- All 11 KB MOVED tables still exist in `sqlite_master` (drop loop did not run)

---

## F9 [LOW] — Rollback recipe completeness

**Files changed:** `scripts/split_ops_migrate.py` (dry-run rollback print + final summary) and `docs/superpowers/plans/split-ops/build-5-report.md`

Updated rollback recipe in both places:
```
1. Stop services:  pkill -TERM -f 'bot\.main'; pkill -TERM -f 'v2/local_server\.py'
2. Restore KB:     cp <backup> <kb_path>
3. Delete OPS DB:  rm gsa_gateway_ops.db
4. Restart on pre-split code: bash scripts/restart.sh
```
Previously omitted steps 1 (stop services) and 3 (delete OPS DB), which would leave a stale OPS causing PK collisions on retry.

---

## Verification Evidence

### 1. Migration tests
```
python3 -m pytest v2/tests/test_split_ops_migrate.py -q
44 passed in 10.91s
```
(41 → 44: +F3 regression test, +F5 wrong-slug gate test, +F8 orchestrator fail-closed test)

### 2. Full suite — net-new failures
```
python3 -m pytest v2/tests/ -q -p no:cacheprovider
```
Output: **117 lines** (equal to baseline `build4_base_fails.txt`). Diff shows only 2 pre-existing lines with a `RuntimeWarning` annotation shifted between them (same tests, same failure mode) — **zero net-new failures**.

### 3. Judging tests
```
python3 -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py v2/tests/test_judging_session.py -q
99 passed in 5.62s
```

### 4. Copy-of-live dry-run
Script ran successfully against a copy of the live DB. Counts match prior proof (posts=431, templates=2, deliveries=1177, events=2, all judging=0). Checksums identical. New guards (busy_timeout, greenfield check, dry-run rollback recipe) did not break the dry-run path. Output ends with `[DRY-RUN] Nothing written.`

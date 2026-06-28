# Build 5 Report — Gated KB→OPS Migration Script

**Branch:** `worktree-split-ops-db` | **Commit:** `6fdd5df` | **Date:** 2026-06-28

---

## What Was Built

`scripts/split_ops_migrate.py` — gated migration that moves the 11 MOVED tables from `gsa_gateway.db` (KB) into `gsa_gateway_ops.db` (OPS).

`v2/tests/test_split_ops_migrate.py` — 41 tests covering all 7 plan tasks.

---

## Gate Function Signature + Every Check

```python
acceptance_gate(
    kb_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    kb_cols_map: dict[str, list[str]],   # table → KB column list (PRAGMA-derived)
    org_slug_map: dict[int, str],         # org_id → slug
) -> dict  # {"passed": bool, "checks": {name: {"status": "PASS"|"FAIL", ...}}}
```

Checks performed (all must pass — fails closed on any single failure):

| # | Check key | What it verifies |
|---|-----------|-----------------|
| 1 | `{table}_count` | KB count == OPS count for every MOVED table |
| 2 | `{table}_checksum` | sha256 over KB cols only — identical on both sides |
| 3 | `{table}_slug_resolved` | No NULL/empty org_slug in posts/post_templates/events |
| 4 | `ops_fk_check` | `PRAGMA foreign_key_check` returns 0 violations in OPS |
| 5 | `r3_invariant` | `create_knowledge_schema` produces NONE of the 11 MOVED tables |

---

## Copy-Fidelity Proof (on a COPY of the live DB)

DB copy: `/tmp/.../kb_copy.db` (213 MB copy of `/home/md724/gsa-gateway/gsa_gateway.db`).

### Before migration (KB)

| Table | Rows | sha256 (first 32 hex) |
|-------|------|----------------------|
| posts | 431 | `ef57edf8cb29a6af...` |
| post_templates | 2 | `e3fe5a1f7ef54c4f...` |
| post_deliveries | 1177 | `e625be261af84920...` |
| events | 2 | `14290ea919c3088a...` |
| event_reminders | 0 | `e3b0c44298fc1c14...` |
| judging_events | 0 | `e3b0c44298fc1c14...` |
| judging_judges | 0 | `e3b0c44298fc1c14...` |
| judging_presenters | 0 | `e3b0c44298fc1c14...` |
| judging_scores | 0 | `e3b0c44298fc1c14...` |
| judging_audience_votes | 0 | `e3b0c44298fc1c14...` |
| judging_score_audit | 0 | `e3b0c44298fc1c14...` |

(Note: post_deliveries shows 1177 not 1180 from the spec — scheduled deletions ran since the spec was written; that is the live count and is correct.)

### After migration — OPS (gsa_gateway_ops.db copy)

All 11 tables present with identical counts. All gate checks: **PASS**.
- `posts_slug_resolved`: PASS — all rows have `org_slug='gsa'`
- `events sqlite_sequence`: seeded to 6 (MAX id of the 2 live events)
- Phase-5 back-fills: `natural_key` updated=0, `ki_content` updated=0 (no event_info rows in live — no-op as expected)

### After migration — KB (kb_copy.db)

All 11 MOVED tables ABSENT from `sqlite_master`. `knowledge_items` present (22,699 rows intact).

### Backup restoration proof

```
cp gsa_gateway.20260628-183407-*.pre-split-ops-migrate.db kb_copy.db
```

After restore: all 11 MOVED tables present with original row counts (431/2/1177/2/0×7). Checksums match pre-migration exactly.

---

## FK-Ordered Drop List

```
1.  DROP TABLE post_deliveries       (FK → posts)
2.  DROP TABLE event_reminders       (FK → events, posts)
3.  DROP TABLE judging_score_audit   (FK → judging_events, judging_judges)
4.  DROP TABLE judging_scores        (FK → judging_events, judging_judges, judging_presenters)
5.  DROP TABLE judging_audience_votes (FK → judging_events)
6.  DROP TABLE judging_presenters    (FK → judging_events)
7.  DROP TABLE judging_judges        (FK → judging_events)
8.  DROP TABLE judging_events
9.  DROP TABLE posts
10. DROP TABLE events
11. DROP TABLE post_templates
```

---

## Rollback Recipe

```bash
# Find the backup (created in .backups/ during --commit run)
ls .backups/gsa_gateway.*.pre-split-ops-migrate.db

# Restore KB to pre-migration state
cp .backups/gsa_gateway.<TIMESTAMP>.pre-split-ops-migrate.db gsa_gateway.db

# Restart services (bots read live KB)
bash scripts/restart.sh
```

The OPS DB (`gsa_gateway_ops.db`) can be deleted after rollback — it holds no data that wasn't in KB.

---

## Net-New Failure Diff (vs baseline build4_base_fails.txt, 117 lines)

```
diff: only 2 lines differ (formatting noise — a RuntimeWarning annotation shifted
      between two pre-existing ERROR lines; same tests, same failure mode)
net-new FAILED or ERROR lines: 0
```

All 41 new tests in `test_split_ops_migrate.py` pass.

## Judging Tests

`python3 -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py v2/tests/test_judging_session.py -q`

**99 passed** (up from 86 in the CLAUDE.md spec — additional tests added in prior builds).

---

## READY FOR OWNER CUTOVER

The migration is proven safe on a copy of the live DB. The owner runs it once when ready to complete the split.

### Step-by-step cutover commands

```bash
# 1. Dry-run first — inspect the plan (no writes)
python3 scripts/split_ops_migrate.py \
  --db gsa_gateway.db \
  --ops-db gsa_gateway_ops.db

# 2. Stop services (optional but recommended to avoid WAL contention)
bash scripts/restart.sh --no-llm   # or kill bots first

# 3. Execute migration (backup → copy → gate → drop)
python3 scripts/split_ops_migrate.py \
  --db gsa_gateway.db \
  --ops-db gsa_gateway_ops.db \
  --commit

# 4. Verify output ends with "Migration COMPLETE" and gate shows all [PASS]

# 5. Restart services pointing to split DBs (Build 2 already wired two-conn)
bash scripts/restart.sh

# 6. If anything is wrong: rollback (see recipe above) and restart
```

### What succeeds = migration done

- All 11 MOVED tables absent from `gsa_gateway.db`
- `gsa_gateway_ops.db` has the tables with correct counts + checksums
- Gate prints all `[PASS]`
- Bots restart without errors

### Operational follow-up (LOW-12)

Once OPS holds live data, add `gsa_gateway_ops.db` to `restart.sh`'s backup rotation alongside `gsa_gateway.db`. Not blocking — flag for next maintenance session.

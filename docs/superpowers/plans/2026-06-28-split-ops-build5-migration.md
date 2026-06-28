# Split-Ops Build 5 — Gated Migration Script + Acceptance Gate (SKELETON)

> **SKELETON.** The most safety-critical phase (immortal posts). Finalize «LOCK» details against
> Build-1/3 reports. REQUIRED SUB-SKILL: superpowers:test-driven-development. **Spec:** §5, Reject
> Criteria, Risks R1/R3. **Phase 5 of 5.** (Live run is the owner-gated cutover, NOT part of this build.)

**Goal:** A gated script `scripts/split_ops_migrate.py` that moves the publishing cluster + judging from
`gsa_gateway.db` into `gsa_gateway_ops.db` with copy → verify(counts + checksum) → fail-closed gate →
drop-last, fully reversible via `hardened_backup`. Dry-run default; `--commit` to write.

**Architecture:** Reuse `hardened_backup` from `scripts/_area_tag_migrate.py:35`. Explicit
column-mapped copy per table (never `SELECT *` — HIGH-2). Checksums computed in Python (sha256 over
ordered, type-normalized rows — SQLite has no md5). Acceptance gate is a pure function returning
pass/fail + a diff, tested for every failure mode.

## Global Constraints
- **Immortal-safe:** drop from the source happens ONLY after the OPS copy is count- AND checksum-verified.
  Any gate failure → abort, drop nothing, leave both DBs inspectable.
- Preserve `id` values exactly (rowid stability for posts/post_deliveries). Carry ALL migration-added
  columns (delete_at/deleted_at; delete_status/deleted_at/delete_error/delete_attempts; judging
  telegram_id_hash/is_present/score_min/max/min_coverage/audience_*). Seed `sqlite_sequence` for `events`.
- Back-fill `org_slug` for posts/events/post_templates (fail if any unresolved). Assert global slug
  uniqueness for referenced slugs (LOW-11).
- Back-fill the EVENT derive natural_key onto existing `event_info` rows (MED-8) so Phase-3 re-derive
  finds, not duplicates, them.
- Pre-drop guard: confirm the HIGH-3 startup repoints are in place (no path re-creates moved tables on KB).
- Two-file backup: `hardened_backup` + `restart.sh` cover the OPS file (LOW-12).
- No Claude/AI attribution. L2 report → `build-5-report.md` only.

## File Structure
- **Create** `scripts/split_ops_migrate.py` — the gated migration (argparse: `--db`, `--ops-db`, `--commit`).
- **Create** `v2/tests/test_split_ops_migrate.py` — gate + copy-fidelity tests on fixtures/copies.
- Possibly extend `scripts/_area_tag_migrate.py` backup rotation / `scripts/restart.sh` for the OPS file.

## Tasks (skeleton)
### Task 1 — column-mapped copy per table (fidelity)
- Test: seed a fixture KB DB with posts (+migration cols), post_deliveries, post_templates, events
  (live shape), event_reminders, all judging_* → copy → OPS rows match source EXACTLY incl `id` and all
  columns; `sqlite_sequence` for events seeded to max(id).

### Task 2 — Python checksum helper
- Test: identical tables → equal checksum; a single changed cell → different; order-independent via `ORDER BY id`.

### Task 3 — org_slug back-fill + resolution gate
- Test: posts/events/post_templates get correct `org_slug`; an org_id with no slug → gate FAIL; a slug
  mapping to >1 org → gate FAIL (LOW-11).

### Task 4 — acceptance gate (fail-closed, every mode)
- Test: gate PASSES on a clean copy; FAILS (and drops nothing) on each of: count mismatch, checksum
  mismatch, unresolved slug, `foreign_key_check` violation in OPS, a moved table still creatable on KB.

### Task 5 — drop-last + reversibility
- Test (on a copy): after gate pass, `--commit` drops moved tables from KB; KB now has none of MOVED
  (invariant); restoring the `hardened_backup` snapshot fully reverts.

### Task 6 — event_info natural_key back-fill (MED-8)
- Test: existing `event_info` rows get the natural_key in metadata; a subsequent `derive_event_kb`
  produces 0 duplicates (reject #7).

### Task 7 — dry-run vs commit + two-file backup
- Test: dry-run writes nothing and prints the plan + projected counts/checksums; `--commit` performs
  backup→copy→gate→drop; the OPS file is included in backup rotation.

## Acceptance (maps to Reject Criteria)
- #1 immortal posts/post_deliveries exact (count+checksum). #2 no cross-DB FK remains (incl post_templates).
- #3 100% slug resolve. #5 no drop before checksum-verify. #6 R3 invariant (moved tables gone from KB).
- #7 no duplicate event_info on re-derive. All proven on a COPY; live run deferred to owner cutover.

## Report → `build-5-report.md`: the gate function signature + checks, copy-fidelity proof on a copy of
## live, the exact drop list, and the rollback recipe. FLAG to orchestrator: ready for OWNER CUTOVER.

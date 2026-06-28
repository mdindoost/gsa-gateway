# Split-Ops Build 5 — Gated Migration Script + Acceptance Gate (FINAL)

> **FINAL** — locked against Build-1..4 reports, `v2/core/database/schema.py`, and the LIVE
> `gsa_gateway.db` (inspected read-only 2026-06-28). The most safety-critical phase (immortal posts).
> REQUIRED SUB-SKILL: superpowers:test-driven-development. **Spec:** §5, Reject Criteria, Risks R1/R3.
> **Phase 5 of 5.** The LIVE run is the owner-gated cutover (#8), NOT part of this build — this build
> PROVES the script on a COPY of live.

**Goal:** A gated script `scripts/split_ops_migrate.py` that moves the publishing cluster + judging from
`gsa_gateway.db` (KB) into `gsa_gateway_ops.db` (OPS) with **copy → verify(counts + checksum) →
fail-closed gate → drop-from-KB LAST**, fully reversible via `hardened_backup`. Dry-run default;
`--commit` to write.

## LIVE GROUND TRUTH (inspected 2026-06-28 — the script must handle THIS, and generalize)
- MOVED row counts: **posts 431, post_deliveries 1180, post_templates 2, events 2**; event_reminders 0;
  **ALL judging_* = 0**.
- **Single org:** every posts/events/post_templates row has `org_id = 2` → slug `gsa`. No NULL org_ids.
  No duplicate org slugs (LOW-11 holds). The script must still FAIL CLOSED if a future row has an
  unresolvable or ambiguous slug.
- **event_info knowledge_items = 0** → the Phase-5 back-fills (ki_content, natural_key) are NO-OPS on
  current data. They must still be implemented + tested on fixtures WITH event_info rows, and must NOT
  error at 0 rows. (With 0 event_info, OPS `events.ki_content` stays NULL → the 2 events derive as
  one-liners later — honest, no regression, no prior content existed.)

## The 11 MOVED tables (canonical — schema.py:33-36)
`posts, post_templates, post_deliveries, events, event_reminders, judging_events, judging_judges,
judging_presenters, judging_scores, judging_audience_votes, judging_score_audit`.

## Column-mapping (EXPLICIT lists — never `SELECT *`, HIGH-2)
Build OPS schema with `create_ops_schema(ops_path)` (schema.py:672) FIRST — it creates every table with
ALL migration columns. Then copy. The OPS shapes ADD columns the KB source lacks:
- **posts**: KB cols = `id,org_id,type,title,content,channels,discord_channel,scheduled_for,sent_at,status,
  source_type,source_id,signature,metadata,created_by,created_at,delete_at,deleted_at`. OPS = same **+
  `org_slug`** (inserted after org_id). Copy all KB cols verbatim; set `org_slug` = resolve(org_id).
- **post_templates**: KB cols = `id,org_id,name,content,post_type,recurrence,channels,discord_channel,
  signature,enabled,last_run_at,next_run_at,metadata,created_by,created_at`. OPS = same **+ `org_slug`**.
- **events**: KB cols (v1 non-STRICT AUTOINCREMENT) = `id,name,date,time,location,description,organizer,
  rsvp_link,category,reminder_sent_7d,reminder_sent_1d,reminder_sent_1h,announcement_sent,channel_posted,
  created_at,created_by,org_id`. OPS = same **+ `ki_content`** (after location, back-filled — NULL now)
  **+ `org_slug`**. **Seed `sqlite_sequence` for `events`** to MAX(id) after copy (AUTOINCREMENT).
- **post_deliveries**: KB cols = `id,post_id,platform,channel,message_id,status,error,sent_at,
  delete_status,deleted_at,delete_error,delete_attempts`. OPS = IDENTICAL → straight column-mapped copy.
- **event_reminders + all judging_***: OPS shapes IDENTICAL to KB → straight column-mapped copy (all empty now).
- **Preserve `id` exactly** on every table (explicit `id` in the INSERT col list) — rowid stability for
  posts/post_deliveries is a hard requirement.
- Derive the KB column list at runtime from `pragma_table_info(<table>)` (don't hardcode and drift); the
  OPS insert list = KB list + the augmented cols (org_slug / ki_content) where applicable. This is robust
  to the live `events` ALTER-appended `org_id`.

## Checksum (Python sha256 — SQLite has no md5)
- Per table: `SELECT <cols> FROM <t> ORDER BY id`, normalize each row to a canonical tuple of strings
  (handle None, int, float, text deterministically — e.g. a fixed `str()` with a distinct NULL sentinel),
  hash the concatenation. Identical content ⇒ equal digest; one changed cell ⇒ different; order-stable.
- **Checksum the COMMON columns ONLY** (the KB source column set) on BOTH sides — EXCLUDE `org_slug`
  (posts/events/post_templates) and `ki_content` (events), which don't exist in KB. This proves the moved
  rows are byte-identical; the augmented cols are verified separately (org_slug resolution gate; ki_content
  back-fill test).

## hardened_backup (reuse — `scripts/_area_tag_migrate.py:35`)
`hardened_backup(db_path, label, keep=10, keep_total=10, backups_dir=None) -> Path` — online-backup +
`PRAGMA integrity_check`, raises if not 'ok', rotates. Take it on the **KB (source)** before any drop:
the snapshot CONTAINS the moved tables → restoring it fully reverts the cutover. At cutover the OPS file
is fresh/greenfield (no OPS backup needed). Note (LOW-12): once OPS holds live data, restart.sh/backup
rotation should also cover the OPS file — flag as an operational follow-up, do not block this build.

## Global Constraints
- **Immortal-safe drop-LAST:** drop the 11 tables from KB ONLY after the OPS copy is count- AND
  checksum-verified AND the full gate passes. Any gate failure → abort, drop NOTHING, leave both DBs
  inspectable, nonzero exit.
- Drop order respects FK (children first): post_deliveries, event_reminders before posts/events;
  judging_score_audit/judging_scores/judging_audience_votes/judging_presenters/judging_judges before
  judging_events. (Or `PRAGMA foreign_keys=OFF` for the drop block — but prefer ordered drops.)
- `--commit` required to write; dry-run prints the plan + projected per-table counts + checksums, writes nothing.
- No Claude/AI attribution. No new deps. L2 report → `build-5-report.md` ONLY (do NOT touch ledger/memory).

## File Structure
- **Create** `scripts/split_ops_migrate.py` — argparse `--db` (default live KB), `--ops-db` (default
  `OPERATIONS_DB_PATH` / `gsa_gateway_ops.db`), `--commit`, optional `--backups-dir` (tests). Structure:
  pure helpers (resolve_slug map, copy_table, table_checksum, acceptance_gate) + a `main()` orchestrator.
- **Create** `v2/tests/test_split_ops_migrate.py` — gate + copy-fidelity tests on fixture/copy DBs
  (use `create_all` for a combined fixture KB seeded with rows; `create_ops_schema` for the OPS target).

## Tasks (TDD — write the test first each task)
### Task 1 — column-mapped copy fidelity
Seed a fixture KB (combined `create_all`) with posts (+delete cols), post_deliveries (+delete cols),
post_templates, events (live shape incl appended org_id), event_reminders, and ≥1 row in each judging_*.
Copy → OPS rows match source EXACTLY incl `id` and all common columns; `org_slug` stamped correctly;
`events` `sqlite_sequence` seeded to MAX(id); judging tables copied row-for-row.

### Task 2 — checksum helper
Identical tables → equal digest; one changed cell → different; order-independent (ORDER BY id);
common-columns-only (adding org_slug/ki_content to the OPS copy does NOT change the digest).

### Task 3 — org_slug resolution gate
posts/events/post_templates get the correct slug from org_id; an org_id with NO slug (or NULL) → gate
FAIL; a slug mapping to >1 org → gate FAIL (LOW-11). Resolve via a single KB `organizations` map.

### Task 4 — acceptance gate (fail-closed, EVERY mode)
Gate PASSES on a clean copy. Gate FAILS (and the orchestrator drops NOTHING) on each of: per-table count
mismatch; checksum mismatch; unresolved/ambiguous slug; `PRAGMA foreign_key_check` violation in OPS;
the HIGH-3/R3 invariant broken (any MOVED table would be (re)created by `create_knowledge_schema` — assert
the KB knowledge-schema builder produces NONE of the 11). Gate returns a structured pass/fail + per-check diff.

### Task 5 — drop-LAST + reversibility
On a COPY: after gate pass + `--commit`, the 11 MOVED tables are gone from KB (`sqlite_master` has none of
them — R3 invariant); the KB still has knowledge_items/nodes/etc intact; restoring the `hardened_backup`
snapshot fully reverts (moved tables + rows back, checksums match original). FK-ordered drop leaves no
dangling references.

### Task 6 — event_info natural_key + ki_content back-fill (MED-8 + B3-1/B3-3)
- **natural_key:** recompute `metadata.natural_key` on every existing `event_info` knowledge_item using
  `event_projection.event_natural_key(name, date, time)` (3-arg; name from the item, date/time from
  `metadata`, time defaulting to "TBD"). Idempotent; 0 rows → no-op.
- **ki_content:** for each existing `event_info`, copy its `content` → the matching OPS `events.ki_content`
  matched by `metadata.ops_event_id` (primary) or `metadata.event_id` (fallback). 0 rows → no-op.
- Test (fixture WITH event_info rows): after back-fill, `event_projection.derive_event_kb(ops, kb,
  org_slugs=("gsa",))` reproduces each event_info's content byte-identically AND yields `created==0`
  (0 duplicates — reject #7). Also assert the 0-event_info case runs clean (matches live).

### Task 7 — dry-run vs commit + plan output
Dry-run writes nothing to KB or OPS and prints per-table planned counts + checksums + the drop list +
the rollback recipe. `--commit` performs backup → copy → back-fills → gate → drop, in that order, and
aborts before drop on any gate failure (nonzero exit, nothing dropped).

## Acceptance (maps to Reject Criteria)
- #1 posts/post_deliveries exact (count+checksum). #2 no cross-DB FK remains (OPS posts.org_id is plain
  INTEGER, no FK; post_templates same). #3 100% slug resolve. #5 no drop before checksum-verify.
  #6 R3 invariant (the 11 gone from KB + not recreatable by create_knowledge_schema). #7 no duplicate
  event_info on re-derive. **All proven on a COPY of live**; the live run is the owner cutover (#8).
- VERIFY also: full v2 suite shows ZERO net-new failures vs the pre-build baseline (orchestrator gate).

## Report → `build-5-report.md`
The gate function signature + every check; copy-fidelity proof on a COPY of the live DB (paste counts +
checksums for all 11 tables, before/after); the exact FK-ordered drop list; the rollback recipe; the
net-new-failure diff; and a clear **"READY FOR OWNER CUTOVER"** flag with the exact commands the owner
will run (dry-run first, then `--commit`).

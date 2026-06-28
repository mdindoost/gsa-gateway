# Split-Ops Build 3 — EVENT→KB Derive + Cross-DB Dashboard Writes (SKELETON)

> **SKELETON.** Finalize «LOCK AFTER P1/P2» signatures before dispatch. REQUIRED SUB-SKILL:
> superpowers:test-driven-development. **Spec:** §3.3, §3.4 (cross-DB ordering), Touch Points. **Phase 3 of 5.**

**Goal:** A GSA event in the OPS DB derives a `knowledge_item` (`type=event_info`, dated) in the
KNOWLEDGE DB — idempotent, rebuildable, one-way. Wire it into the dashboard create-event flow and the
`add_to_kb` post flow as OPS-commit-first cross-DB writes. Reproduces today's behavior; NO retrieval change.

**Architecture:** New `v2/core/publishing/event_projection.py` with `derive_event_kb`. **REUSE
`resolve_org` from `v2/core/publishing/org_resolve.py` (Build 2) — do NOT redefine.** Keyed by a stable
`(org_slug, normalized name + date)` natural key (with `ops_event_id` as an informational secondary match
during transition — MED-8). Embedding rides the existing `embed_all` pass (Q1: inline-embed the single
item in the dashboard flow — lean yes).

**LOCKED inputs from Build 1/2 (verified):**
- Two-DB test fixture: `two_db` in `v2/tests/test_build2_split_ops.py` returns
  `{"kb_conn","ops_conn","kb_path","ops_path"}` (GSA org + settings seeded in KB). EXTEND it with
  `ops_conn.execute("INSERT INTO events(...)")` for derive tests.
- OPS `events` columns (live shape): `id, name, date, time, location, description, organizer, rsvp_link,
  category, reminder_sent_*, announcement_sent, channel_posted, created_at, created_by, org_id, org_slug`.
  `events.org_slug` is present → derive reads it directly (no resolve needed for the event's own org).
- `local_server.py` ALREADY has `_conn()` (KB) and `_ops_conn()` (OPS, Build 2 F2). `_create_event` /
  `_post_post` write cluster rows via `_ops_conn()` then the derived `knowledge_item` via `_conn()`.
- Cross-DB ordering (MED-9): commit OPS first, then KB derive; KB failure → log + rebuildable (never reverse).

## Global Constraints
- One-way only: OPS event → KB item. Never write back to OPS from the derive.
- Idempotent: re-running creates 0 duplicates. Removed/renamed event → deactivate (`is_active=0`) stale item.
- GSA-only: `org_slugs=("gsa",)` is the single scope param (extension point for clubs later).
- Cross-DB write ordering: **commit OPS first, then KB derive**; KB-write failure logs a warning and
  leaves a rebuildable gap (never lose the OPS row, never write a KB item with no backing OPS event).
- No retrieval/answer code changes (RAG-review trigger stays off). No Claude/AI attribution.
- L2 report → `build-3-report.md` only.

## File Structure
- **Create** `v2/core/publishing/event_projection.py` — `derive_event_kb`, `resolve_org`, `event_natural_key`.
- **Create** `scripts/derive_event_kb.py` — gated re-derive-all (dry-run default, `--commit`, optional `--embed`).
- **Modify** `v2/local_server.py:922` `_create_event` — events/posts/reminders → OPS (commit), then `derive_event_kb` → KB.
- **Modify** `v2/local_server.py:891` `_post_post` — post → OPS (commit), then (if `add_to_kb`) the knowledge_item → KB.
- **Test** `v2/tests/test_event_projection.py`.

## Tasks (skeleton)
### Task 1 — `event_natural_key` + `resolve_org`
- Test: natural key is stable under whitespace/case noise in `name`; differs by `date`. `resolve_org` returns org row, fails on unknown / >1.

### Task 2 — `derive_event_kb` creates an event_info item (GSA-only)
- Test: a GSA OPS event → one KB `knowledge_item` type `event_info`, dated, with `metadata.derived_from='ops_event'`, `org_slug`, `ops_event_id`, natural_key. A non-GSA event → no item.

### Task 3 — Idempotency + transition match (MED-8)
- Test: running twice → still ONE item (matches on natural_key). Pre-seed a legacy `event_info` row keyed only by `metadata.event_id` → derive matches it (secondary `ops_event_id` match) and does NOT duplicate.

### Task 4 — Reconcile removed/renamed events
- Test: delete the OPS event → derive deactivates (`is_active=0`) the stale item. Rename → old item deactivated, new natural_key item created.

### Task 5 — `_create_event` cross-DB (OPS-commit-first)
- Today (`local_server.py:932`) `_create_event(conn, b)` inserts events + event_info knowledge_item +
  event_announcement post + reminders in ONE `conn`. Split: events/post/reminders → `self._ops_conn()`,
  derived `event_info` → `self._conn()` (KB) via `derive_event_kb`. **Stamp `org_slug` on the OPS
  events + posts INSERTs** — look it up once from KB by `b["org_id"]` (`SELECT slug FROM organizations
  WHERE id=?`); these INSERTs currently omit it and would fall back to the schema default `'gsa'`.
- Test (two-DB fixture): create a GSA event via the handler → events/posts/reminders in OPS (with correct
  org_slug), one `event_info` in KB. Simulate KB-write failure → OPS event persists, warning logged,
  re-derive script repairs it.

### Task 6 — `_post_post(add_to_kb)` cross-DB
- Test: post → OPS; `add_to_kb` → KB knowledge_item; ordering OPS-first; failure leaves OPS post intact.

### Task 7 — `scripts/derive_event_kb.py` gated re-derive
- Test: dry-run reports planned derives, writes nothing; `--commit` (on a copy) derives all GSA events idempotently; re-run = 0 new.

## Acceptance
- Re-derive over a migrated-style DB yields 0 duplicate `event_info` rows (reject criterion #7).
- Event answers unchanged vs today (derived items byte-equivalent to current `event_info`).

## Report → `build-3-report.md`: `derive_event_kb`/`resolve_org`/`event_natural_key` signatures + metadata schema (Phase 5 migration back-fills the same natural_key onto existing rows).

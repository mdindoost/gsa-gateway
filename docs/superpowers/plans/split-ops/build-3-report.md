# Split-Ops Build 3 — EVENT→KB Derive Report

**Commit:** d37df05  
**Branch:** worktree-split-ops-db  
**Status:** COMPLETE — 21 tests, 0 net-new failures

---

## Signatures (Phase 5 needs these)

### `event_natural_key(name: str, date: str) -> str`
Stable derive key. Normalization: strip → collapse whitespace → lowercase; concatenate with `|date`.
```
event_natural_key("Spring Social", "2026-04-10")  →  "spring social|2026-04-10"
event_natural_key("  SPRING  SOCIAL  ", "2026-04-10")  →  "spring social|2026-04-10"
```

### `derive_event_kb(ops_conn, kb_conn, *, org_slugs=("gsa",)) -> dict`
Returns `{"created": int, "updated": int, "deactivated": int}`.
- Source: `events WHERE org_slug IN org_slugs` (OPS DB)
- Target: `knowledge_items` type `event_info` (KB DB)
- Commits per org-slug iteration
- Never writes back to OPS

### `resolve_org` — REUSED from `v2/core/publishing/org_resolve.py` (not redefined)

---

## Metadata Schema Written to KB

Every derived `event_info` item carries:
```json
{
  "derived_from": "ops_event",
  "org_slug":     "gsa",
  "ops_event_id": 7,
  "date":         "2026-04-10",
  "time":         "6:00 PM",
  "natural_key":  "spring social|2026-04-10"
}
```

**Phase 5 migration must back-fill** the same `natural_key` onto pre-Phase-3 rows that only have `metadata.event_id` (the MED-8 transition path). After back-fill, re-running `derive_event_kb` yields 0 net-new rows (proven by `test_derive_zero_new_rows_on_already_derived_db` and `test_derive_matches_legacy_event_id_med8`).

---

## MED-8 Transition Match

Pre-Phase-3 rows have `metadata.event_id` (OPS rowid) but **no** `metadata.natural_key`. During derive:
1. Primary match: `json_extract(metadata,'$.natural_key') = nk`
2. MED-8 fallback (only for rows where `natural_key IS NULL`):
   `json_extract(metadata,'$.event_id') = ops_event_id OR json_extract(metadata,'$.ops_event_id') = ops_event_id`

The `IS NULL` guard is critical: it prevents a renamed event (which has a natural_key from a prior derive run but a different name) from matching the fallback — ensuring the old item is deactivated and a new one is created instead.

---

## Cross-DB Write Ordering (MED-9)

Both `_create_event` and `_post_post` follow: **OPS commit → KB write**.

`_create_event`:
1. Resolve `org_slug` from KB (by `org_id`)
2. OPS: INSERT event + announcement post + reminders → `ops_conn.commit()`
3. KB: `derive_event_kb(ops_ro, conn, org_slugs=(org_slug,))`
4. KB failure → `logger.warning(...)` only; OPS rows stand; rebuildable via `scripts/derive_event_kb.py`

`_post_post` (non-event):
1. Resolve `org_slug` from KB
2. OPS: INSERT post → `ops_conn.commit()`
3. KB: INSERT knowledge_item if `add_to_kb` (caught separately; OPS post stands on failure)

**Backward-compat combined-DB mode:** both methods check `getattr(self, '_ops_conn', None)` before calling it. If not callable (e.g., tests using `object()` as self with a combined DB), `conn` is used for OPS writes. All pre-existing `test_local_server_delete_at.py` tests pass unchanged.

---

## Test Proving No-Duplication

`test_derive_zero_new_rows_on_already_derived_db` (Task 3, reject criterion #7):
- Seeds 2 OPS events
- Calls `derive_event_kb` twice
- Asserts `count_after == count_before` (0 net-new `event_info` rows)

`test_derive_matches_legacy_event_id_med8` (MED-8):
- Pre-seeds a legacy `event_info` with only `metadata.event_id`
- Calls `derive_event_kb` once
- Asserts exactly 1 `event_info` row (not 2), with `natural_key` back-filled

---

## Test Name-Set Diff (Zero Net-New Failures)

Pre-existing failures in v2/tests (before Build 3): 44 tests  
After Build 3: 44 tests — **same set**  

Pre-existing failures in bot/tests (before Build 3): 12 tests  
After Build 3: 12 tests — **same set**  

New tests added: 21 (`v2/tests/test_event_projection.py`), all passing.

Pre-existing failures confirmed by git stash + rerun (test_local_server.py was already 6/7 failing; test_local_server_delete_at.py was 0 failing pre-changes and remains 0 failing post-changes after combined-DB backward-compat fix).

---

## Files Created / Modified

| File | Change |
|---|---|
| `v2/core/publishing/event_projection.py` | NEW — `event_natural_key`, `derive_event_kb` |
| `scripts/derive_event_kb.py` | NEW — gated re-derive-all (dry-run default, --commit, --embed) |
| `v2/local_server.py` | MODIFIED — `_create_event` + `_post_post` split to cross-DB; import `derive_event_kb` |
| `v2/tests/test_event_projection.py` | NEW — 21 TDD tests (Tasks 1–7) |

---

## Deferred / Phase 5 Notes

- **Back-fill migration (Phase 5):** must write `natural_key` into `metadata` of existing `event_info` rows before running `derive_event_kb` in production, OR let the first `derive_event_kb` run do the back-fill via the MED-8 fallback path (it updates the row + writes `natural_key`). Either approach works; the MED-8 fallback is the safest path since it handles it automatically.
- **Inline embedding:** not added (plan said "lean yes" but this is a `--embed` flag on the re-derive script; normal embedding rides the existing `embed_all` pass).
- **`org_id` on `events`/`posts` INSERTs:** retained (informational column, no FK); `org_slug` is now stamped explicitly from KB lookup on every `_create_event` and `_post_post` call.

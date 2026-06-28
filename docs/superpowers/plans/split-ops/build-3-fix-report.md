# Build 3 — Fix Report (B3-1 through B3-5)

**Branch:** `worktree-split-ops-db`  
**Date:** 2026-06-28  
**Verdict:** All five findings resolved; zero net-new test failures.

---

## Summary

| Finding | Severity | Status | Commit |
|---------|----------|--------|--------|
| B3-5 | LOW    | Fixed  | `6242e71` |
| B3-3 | MED    | Fixed  | `fd59671` |
| B3-1 | HIGH   | Fixed  | `0c4703b` |
| B3-2 | MED    | Fixed  | `0c4703b` |
| B3-4 | doc    | Fixed  | `0c4703b` |

---

## B3-5 — Isolation tests use specific OperationalError (commit `6242e71`)

**Files:** `v2/tests/test_event_projection.py`

Replaced bare `except Exception: pass` in the "KB must not have events/posts table"
assertions (at the old lines ~423 and ~520) with:

```python
except sqlite3.OperationalError as exc:
    assert "no such table" in str(exc).lower(), f"..."
```

An `AssertionError` inside the `try` block can no longer be silently swallowed.

---

## B3-3 — Natural key includes time (commit `fd59671`)

**Files:** `v2/core/publishing/event_projection.py`, `v2/tests/test_event_projection.py`

### Final `event_natural_key` signature

```python
def event_natural_key(name: str, date: str, time: str) -> str:
```

Key format: `"{normalized_name}|{date}|{time}"` where `normalized_name` is
whitespace-collapsed, lowercased. `date` and `time` are verbatim.

Example:
```
event_natural_key("Spring Social", "2026-04-10", "6:00 PM")
  → "spring social|2026-04-10|6:00 PM"

event_natural_key("Spring Social", "2026-04-10", "8:00 PM")
  → "spring social|2026-04-10|8:00 PM"   # different key
```

`derive_event_kb` now calls: `event_natural_key(evt["name"], evt["date"], evt["time"] or "TBD")`.

Two new tests added:
- `test_event_natural_key_differs_by_time`
- `test_derive_same_name_same_date_different_time_two_kb_rows`

All existing `event_natural_key` call sites updated to pass the 3rd `time` arg.

### Phase-5 migration note (MUST BACK-FILL)

The existing `event_info` rows in the live KB have `metadata.natural_key` computed
with the OLD two-argument formula (`"{name}|{date}"`). Phase-5 migration MUST
re-compute and back-fill `metadata.natural_key` on ALL existing `event_info` rows
using the NEW three-argument formula:

```python
event_natural_key(row["title"], meta["date"], meta["time"])
```

The `time` value is already present in `metadata.time` on every row (it was
stored there from Build 3).

---

## B3-1 — ki_content preserved in KB content (commit `0c4703b`)

**Files:** `v2/core/database/schema.py`, `v2/core/publishing/event_projection.py`,
`v2/local_server.py`, `v2/tests/test_event_projection.py`

### Schema change — `ki_content TEXT` on OPS `events`

Added to `OPS_EVENTS` DDL (new tables) and to `_OPS_COLUMN_MIGRATIONS` (existing DBs):

```python
("events", "ki_content", "TEXT"),
```

This is additive and safe on existing OPS databases.

### `_create_event` persists `ki_content`

```python
"INSERT INTO events(..., ki_content, ...) VALUES (..., ?, ...)",
(..., b.get("ki_content") or None, ...)
```

### `derive_event_kb` selects and uses `ki_content`

OPS fetch now includes `ki_content`:
```python
"SELECT id, name, date, time, location, ki_content FROM events WHERE org_slug=?"
```

Content for INSERT: `evt["ki_content"] or one_liner`

Content for UPDATE (no-overwrite rule):
```python
if evt["ki_content"]:
    update_content = evt["ki_content"]
elif existing["content"]:
    update_content = existing["content"]   # preserve existing rich text
else:
    update_content = one_liner
```

This means: if OPS `ki_content` is NULL/empty AND the existing KB row already
has non-empty content, the existing content is kept. The one-liner is only used
when both OPS `ki_content` and the existing KB content are absent/empty.

### Phase-5 migration note (MUST BACK-FILL)

The OPS `events` rows copied by the Phase-5 migration will have `ki_content = NULL`
until back-filled. **Phase-5 must back-fill `OPS events.ki_content` from the
existing `KB knowledge_items.content` for every row that was a derived
`event_info`** (i.e. rows with `created_by='derive_event_kb'` or
`json_extract(metadata,'$.derived_from')='ops_event'`).

SQL sketch:
```sql
UPDATE ops.events
SET ki_content = (
    SELECT ki.content
    FROM kb.knowledge_items ki
    WHERE ki.type = 'event_info'
      AND json_extract(ki.metadata, '$.ops_event_id') = ops.events.id
      AND ki.content IS NOT NULL AND ki.content != ''
    LIMIT 1
)
WHERE ki_content IS NULL;
```

Without this back-fill, a Phase-5 rebuild re-derive will produce one-liners for
all existing events rather than reproducing today's richer content.

### Four tests added

- `test_derive_ki_content_is_used_as_kb_content`
- `test_derive_ki_content_null_uses_one_liner`
- `test_derive_ki_content_null_does_not_overwrite_existing_rich_content`
- `test_create_event_persists_ki_content_to_ops`

---

## B3-2 — GSA-only live derive in `_create_event` (commit `0c4703b`)

**File:** `v2/local_server.py`

`_create_event` now guards the KB derive with:

```python
if org_slug == "gsa":
    # ... derive_event_kb(...)
```

Non-GSA events are still written to OPS (the event + announcement post are
always created). Only the KB `event_info` derive is skipped for non-GSA orgs.
This matches the existing behaviour of the bulk `scripts/derive_event_kb.py`
script which defaults to `org_slugs=("gsa",)`.

One test added: `test_create_event_non_gsa_skips_event_info_derive`

---

## B3-4 — `_post_post` docstring corrected (commit `0c4703b`)

**File:** `v2/local_server.py`

`_post_post`'s docstring and the inline warning log no longer describe the
`announcement` knowledge_item as "rebuildable". Corrected wording:

> This item is manual content, preserved through rebuilds as manual content —
> it is NOT auto-derived from the post. Re-running _post_post would create a
> second post; it is NOT an idempotent derive. On KB write failure the OPS post
> stands and the KB item can be re-added manually.

No behaviour change.

---

## event_info content/metadata schema (current, post-fix)

```json
{
  "derived_from": "ops_event",
  "org_slug": "gsa",
  "ops_event_id": 42,
  "date": "2026-04-10",
  "time": "6:00 PM",
  "natural_key": "spring social|2026-04-10|6:00 PM"
}
```

Content: `evt["ki_content"]` if non-empty, else `"{name} — {date} at {time}, {location}."`.

---

## Phase-5 back-fill requirements (both flagged above)

1. **`OPS events.ki_content` back-fill**: for every OPS event that has a
   corresponding `KB event_info`, copy `KB event_info.content` → `OPS events.ki_content`.
   Without this, a post-wipe rebuild re-derive produces one-liners not today's content.

2. **`metadata.natural_key` back-fill**: re-compute and update `natural_key` on
   ALL existing KB `event_info` rows from the OLD two-arg formula
   (`"{name}|{date}"`) to the NEW three-arg formula
   (`"{normalized_name}|{date}|{time}"`).
   The `time` field is already in `metadata.time` on every row.

---

## Test verification

```
v2/tests/test_event_projection.py: 28 passed (7 new, 21 existing — all green)
v2/tests/test_judging_{db,calculator,session}.py: 99/99 passed
bot/tests: unchanged (no bot test changes)
```

### Failure set diff (v2/tests suite)

- Failing tests BEFORE this session: 44 (pre-existing, listed in review findings)
- Failing tests AFTER this session: 44 (identical set)
- Net-new failures: **0**
- Regressions introduced: **0**

Re-derive-twice idempotency confirmed manually: first derive creates 3 rows,
second derive creates 0 rows (updates 3), active count stable.

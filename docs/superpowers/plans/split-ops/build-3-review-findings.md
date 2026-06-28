# Build 3 — Review Findings (orchestrator + Codex) → FIX SET

**Verdict: CHANGES-REQUIRED.** Core derive (idempotency, MED-8 transition match, MED-9 OPS-commit-first
ordering, conn lifecycle) is correct + verified. But the derive does NOT faithfully reproduce today's
event KB content, plus disambiguation + test gaps. Orchestrator-verified against code. Codex tree-write
check: clean. Zero net-new test failures, 23 derive tests pass (but see B3-5).

## MUST FIX
**B3-1 (HIGH) — derive drops today's `ki_content` → event KB content regression (violates "no retrieval change").**
Today `_create_event` set `event_info.content = b.get("ki_content") or "<one-liner>"` (the officer's rich KB
text). The new flow stores only `description` in OPS events and `derive_event_kb` selects only
`id,name,date,time,location` (`event_projection.py:95`) and always builds the one-liner (`:109`). So any event
with custom `ki_content` loses it in KB → answers degrade. VERIFIED: `_create_event` no longer captures
`ki_content` at all; derive never reads `description`.
FIX: persist the KB content in OPS (add `ki_content TEXT` to OPS `events`, or store the composed
`ki_content or one-liner`), and have `derive_event_kb` reproduce it exactly (`content = evt.ki_content or
one-liner`). The derived item must be byte-identical to today's for the same input.

**B3-3 (MED) — natural key = name+date only → distinct same-name/same-date events collapse.**
Two events (same org, same name, same date, different time/location) map to one natural_key and overwrite
each other in KB (`event_projection.py:43` + `:123`). FIX: include `time` (and/or location) in the natural key.

**B3-5 (LOW) — isolation tests catch bare `Exception` → mask their own `assert False`.**
`test_event_projection.py:423,520` "KB has no events/posts" assertions catch `Exception`, swallowing the
test's own assertion failure → they pass even if the forbidden table exists. FIX: catch
`sqlite3.OperationalError` and assert "no such table" (or query `sqlite_master`).

## DECISION (owner 2026-06-28): **GSA-only everywhere (Option A).** `_create_event` skips the live derive
unless `org_slug=='gsa'`; rebuild stays GSA-only. Non-GSA dashboard events no longer get an `event_info`.
**B3-2 (MED) — scope inconsistency: live derive vs rebuild re-derive.**
`_create_event` derives for the event's OWN org (`org_slugs=(that slug,)`), but `scripts/derive_event_kb.py`
+ the rebuild default to GSA-only (`:73`). So a non-GSA dashboard event gets a live KB `event_info` that a
default rebuild will NOT recreate → it silently vanishes on rebuild. Owner already chose "GSA-only projection."
OPTIONS: (A) GSA-only everywhere — skip live derive unless `org_slug=='gsa'` (matches the decision; changes
today's behavior for non-GSA dashboard events: they'd no longer get event_info); (B) scope-match — rebuild
re-derives every org that has events (keeps today's per-org behavior). Recommend (A).

## DOC-ONLY
**B3-4 (MED→doc) — `_post_post(add_to_kb)` is not a rebuildable projection.** It's a MANUAL KB add (one
`announcement` knowledge_item, `created_by='dashboard'`), preserved by the rebuild as manual content — not
auto-derived from the post. Re-running the handler making a new post is by-design (creating a post twice =
two posts). FIX: correct the plan/comment wording — don't claim `_post_post` is a rebuildable projection;
it's a cross-DB write (post→OPS, manual item→KB) where on KB failure the post stands and the manual item is
re-addable by hand. (No code change beyond the comment.)

## Acceptance for the fix
- Derived `event_info` content byte-identical to today's for the same input (ki_content preserved); a test
  with custom ki_content asserts it survives the derive.
- natural key disambiguates same-name/same-date events (test).
- isolation tests use specific exception + real assertion.
- B3-2 resolved per owner choice (default GSA-only live derive if (A)).
- Zero net-new failures (in-location), judging 99/99.

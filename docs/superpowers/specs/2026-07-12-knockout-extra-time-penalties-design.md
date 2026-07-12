# Knockout coverage: extra time + penalty shootouts — design

**Date:** 2026-07-12
**Author:** Mohammad (VP AA) + Claude
**Status:** BUILT (TDD) — reviewed by senior-eng agent + Codex (both APPROVE-WITH-CHANGES,
all fixes folded in); pending Mohammad's diff sign-off before commit + restart.
**Area:** WorldCup live watcher (`v2/integration/match_watcher.py`, `v2/integration/wc_providers/*`)
**Provider:** ESPN (`WC_PROVIDER=espn`, the live engine)

## Problem

The World Cup has entered the **knockout stage**, and the live watcher silently stops
reporting any match that goes past 90 minutes. Three confirmed real instances (all from the
live ESPN feed, verified 2026-07-12):

| Match | ID | Date | Outcome | What we posted | What we missed |
|---|---|---|---|---|---|
| SUI @ ARG | 760513 | 07-11 | **3–1 AET** | preview, kickoff, goals 10' + 67' (1–1) | 2 extra-time goals, full-time |
| ENG @ NOR | 760512 | 07-11 | **1–2 AET** | preview, kickoff, goal 93' | extra-time goal(s), full-time |
| COL @ SUI | 760508 | 07-07 | **0–0, SUI win 4–3 pens** | preview, kickoff | shootout + full-time (0–0 → nobody noticed) |

User-visible symptom (reported for Arg–Swi): "after 9 pm the bot stopped reporting the game."

**This is NOT caused by the server migration.** Verified clean: system TZ is
`America/New_York (EDT)`; ESPN reachable from the new host (HTTP 200, 0.07 s); no ESPN
blocks / fetch errors / loop crashes in the logs overnight; the bot process stayed up all
night (09:00 scheduler tick). The pipeline worked end-to-end — preview, kickoff and every
regulation goal posted correctly. The bug is a pre-existing design limit that the group
stage never exercised (group matches can't go to extra time).

## Root causes

### Cause 1 — `MATCH_MAX` retires the match mid-overtime (the dominant cause)
`match_watcher.py:77` — `MATCH_MAX = timedelta(hours=2, minutes=30)`, the "safety stop after
kickoff". `_retire_active` (`:535`) drops a match when `finished OR now >= kickoff + MATCH_MAX`.

A 90-minute match fits inside 2h30m; a knockout match that goes to **extra time runs
~2h45m–3h**, and **with penalties ~3h–3h20m** of wall-clock. So the safety stop fires *while
the match is still being played*, retiring it as "expired, unfinished." Every subsequent tick
skips it; the extra-time goals and the full-time are never seen.

Evidence: Arg–Swi kickoff 21:01 → `MATCH_MAX` deadline ≈ 23:31; the ESPN state file froze at
exactly `23:30:12` with `finished:false, score:[1,1]`.

### Cause 2 — penalty scoreline is never captured
Both final statuses map correctly to canonical `"done"` (verified):
- Extra time: `STATUS_FINAL_AET` → `state:"post", completed:true` → `"done"`, `score = 3–1`.
- Penalties: `STATUS_FINAL_PEN` → `state:"post", completed:true` → `"done"`, `score = 0–0`.

So `process_match`'s done-branch *would* post a full-time (if we were still watching). BUT for
a penalty match the full-time uses `norm.score`, which ESPN populates with the
**regulation/AET score (0–0)**. The shootout result lives in a **separate per-competitor field
`shootoutScore`** (SUI 4 / COL 3), plus a `winner: true` flag — and `NormMatch` does **not**
capture it (`normalize.py` reads only `score`). Result: even a delivered penalty full-time
would read "0–0" with no winner.

### Non-cause — shootout status handling
`process_match` intentionally emits nothing during `state == "shootout"` (`espn_process.py:116`,
"never walk shootout kicks as goals"). That is correct and stays. We report the *result*, not
each kick.

## Proposed fix

### Fix A — timing: raise `MATCH_MAX` to cover overtime + shootout  ✅ BUILT (4h)
Change `MATCH_MAX = timedelta(hours=2, minutes=30)` → **`timedelta(hours=4)`**.

**Do we even need the cap? Yes — and this clarifies its purpose.** The cap is NOT how a match
finishes normally (the `finished` flag retires a completed match the instant we read `done`).
The cap only bounds a match we **never see finish** — a missed/absent `done` read (ESPN drops
the row post-match, or the bot/circuit-breaker was down during the exact FT window) or an
abandoned/suspended match. Without it, such a match stays in `_active` forever: its ET-day
stays in every fetch so the watcher never goes idle, hammering the keyless block-prone ESPN
endpoint until it's 429-blocked, and the active set only grows. So the cap is a resource/
blocking backstop; 4h from *scheduled* kickoff clears the longest real knockout (120' +
shootout + breaks + stoppage + a kickoff delay ≈ 3h20m) while still bounding a stuck match.

Plus a **never-started HOT-spin guard** (review F1): `_poll_interval` returned HOT (2s) for any
active-but-not-`started` match; a postponed match would now HOT-spin for the whole 4h window.
Fixed to HOT only until `KICKOFF_GRACE` past kickoff, then COOL.

Why a plain constant bump is safe and sufficient (not a live-extend state machine):
- `_retire_active` already retires a match **immediately** on the `finished` flag, the moment
  we observe `"done"`. So a *normal* match that ends at ~2h is dropped at ~2h regardless of
  `MATCH_MAX`. Widening the cap only affects matches we **never see finish** — exactly the
  overtime case we want to keep watching, plus genuine feed failures.
- Cost of the wider cap: a phantom/never-finished match is polled at `COOL_INTERVAL` (25 s,
  one *shared* fetch — no per-match API cost) for 75 min longer. Negligible.
- 3h45m generously covers 90 + 15 half + ~5 ET break + 30 ET + breaks + stoppage + shootout.
- `MATCH_MAX` also bounds the `_select_active` watch window (`:518`, `:134`), so widening it is
  restart-safe: a bot restart mid-extra-time can still re-select the live match.

`HOT_WINDOW` / `HOT_INTERVAL` are unaffected — an ET goal marks the match HOT and tightens
cadence exactly as a regulation goal does.

**Alternative considered (rejected for now): "extend while live"** — track `last_live_seen`
per match, keep active past `MATCH_MAX` only while the feed still reports it live, hard-cap at
4h. More precise but adds ledger state + edge cases (feed flicker, restart) for little gain
given the "finished retires immediately" insight above. Documented so the reviewer can push
back if they disagree.

### Fix B — penalties + AET: capture finish metadata and render it  ✅ BUILT
1. `normalize.py`: add THREE fields to `NormMatch` — `finish_kind` (regulation/aet/penalties),
   `shootout_score: tuple|None`, `winner_side: "home"/"away"/None`. New `_finish_meta` (run only
   on a `done` read) recovers them from the raw `status.type.name` BEFORE `_canon_status`
   collapses AET/PEN/regular all to `"done"` (review B2/F3 — the AET marker is otherwise
   unbuildable). `shootout_score` is captured ONLY for a penalty final and ONLY when BOTH sides
   parse (review: never coerce a missing pen score to 0). `winner_side` is derived from the
   **higher `shootoutScore`** (the literal result — Mohammad's call: shootoutScore primary);
   tie/absent → None (no guessed winner).
2. `espn_process.py` done-branch: attach `aet: True` / `shootout_score` + `winner_side` as their
   OWN event keys — **never `uid`** (review F5: `uid` would change the durable `{match}:fulltime:`
   dedup key and risk a restart double-post).
3. `format_event` (**`v2/integration/worldcup_tracker.py`** — the LIVE one; review F2/B1 corrected
   from the dead `bot/services/worldcup_tracker.py`) fulltime branch renders, appended under the
   score line: `_Switzerland win 4–3 on penalties_` / `_After extra time_` / (ambiguous winner)
   `_Decided on penalties (N–M)_`. No new keys → plain FULL-TIME, unchanged (football-data safe).
4. AET marker — folded into #1's `finish_kind` (needed the extra NormMatch field, per B2/F3).

### Deferred (explicitly, not silently dropped)
- **Live shootout kick-by-kick** — keep emitting nothing during the shootout; report only the
  result. (Scope control; revisit if desired.)
- **Extra-time half labels** beyond "Extra Time" (`_half_label` already returns "Extra Time"
  for >90'; ET goals will post with that label — acceptable).

## Goals checklist (verified by the test suite — 59 passing incl. 17 new)
- [x] AET matches post every extra-time goal + a full-time (Fix A; ET-goal-label test)
- [x] Penalty matches post a full-time **with the shootout winner + score** (Fix A + B1–B3;
      real-fixture end-to-end render verified: "Switzerland win 4–3 on penalties")
- [x] AET full-time is marked "After extra time" (Fix B4 via `finish_kind`)
- [x] No regression to group-stage / regulation matches (finished-flag retire immediate;
      fulltime dedup-key-unchanged test; regulation-plain/backward-compat test)
- [ ] Live shootout kick-by-kick — **DEFERRED** (non-goal; report the result, not each kick)

## Test plan (TDD)
- Unit: `process_match` done-branch with a `STATUS_FINAL_PEN` NormMatch carrying
  `shootout_score=(4,3)` → fulltime event includes winner + shootout score.
- Unit: `event_to_match` populates `shootout_score` from a real 760508 payload fixture; `None`
  for a regulation/AET fixture (760513).
- Unit: `_retire_active` does NOT drop a still-`in_play` match at old-2h30m but does at 3h45m;
  drops a `finished` match immediately regardless.
- Fixtures: capture real 760508 (PEN) and 760513 (AET) scoreboard payloads into
  `v2/tests/fixtures/`.

## Open questions for reviewers
1. Constant bump vs "extend-while-live" — is 3h45m the right cap, or do you want the
   live-extend approach for correctness under a stuck feed?
2. Winner determination: trust competitor `winner:true`, or derive from higher
   `shootoutScore` (or both, with a mismatch guard)?
3. Any concern with widening the `_select_active` window (idle discovery / restart-safety)?

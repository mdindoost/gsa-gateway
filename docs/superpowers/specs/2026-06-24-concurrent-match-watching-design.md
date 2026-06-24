# MatchWatcher — Concurrent Match Watching (active-set driver)

**Date:** 2026-06-24
**Status:** Design — folded senior-eng review; pending owner sign-off (per the expert-review hard gate)
**Author:** Mohammad Dindoost (w/ Claude)
**Component:** `v2/integration/match_watcher.py`
**Review:** senior-eng design review = SHIP-WITH-FIXES; all must-fixes folded (see "Review fixes folded" at end).

## Problem

`MatchWatcher` watches **one match at a time, serially.** The scheduling loop
(`_loop`) picks the single soonest not-yet-finished match via `_next_kickoff`,
then `await self._watch(...)` **blocks** on that match for its entire duration
(up to `MATCH_MAX` = 2h30m). While blocked, no other match is watched.

When two (or more) matches kick off at the same time — which happens **every
group's final matchday by FIFA design** (simultaneous kickoffs prevent collusion;
the WC 2026 group stage has paired same-time kickoffs throughout, e.g. the
2026-06-24 Group A/B days in `wc_schedule.py`) — only ONE game is covered. The
other gets **no preview, no kickoff, no goals**. By the time the first match
finishes and `_loop` comes back around, the second simultaneous match is usually
already `FINISHED`, so `_next_kickoff` filters it out (`_canon == "done"`) and it
receives **zero posts**.

## Goal

Cover **every** concurrent match fully and symmetrically — preview + kickoff +
each goal + corrections + full-time — for as many simultaneous games as occur
(up to ~8 on a group-finale day), **without increasing API-call cost** beyond
what watching a single match costs today, AND catching kickoffs/goals as fast or
faster than today.

### Non-goals (explicitly deferred)
- **Preview consolidation / "matchday digest"** to reduce the pre-kickoff wall of
  N simultaneous previews. Per-game previews ship as-is. If the flood proves
  annoying in practice, a digest is a clean isolated follow-up — NOT built here.
- Any change to **what** is posted or **how** events are decided. The state
  machine `_process` is untouched (see below).

## Key insight that makes this free

`_fetch_match` (lines 176–182) does **not** fetch a single match — it calls
`/competitions/WC/matches?dateFrom={day}&dateTo={day+1}` (the whole day's
fixtures) and then filters out the one match by id, discarding the rest. So a
single API call already carries the live status + score of **every game that
day.** Therefore covering K simultaneous games costs the **same** number of API
calls as covering 1 — provided one shared fetch feeds all of them.

**Freshness is already proven:** every kickoff/goal post the bot makes today is
already powered by this exact day-window payload (the single-match path just
discards the other games). Fanning out to all games reads the identical rows at
identical freshness — there is no new, unproven data source. (A `FOOTBALL_DEBUG_LOG`
capture on a live concurrent matchday is a cheap belt-and-suspenders confirmation,
not a blocker.)

The constraint is "API calls **per poll tick**," which is constant regardless of
how many games are live — NOT "API calls per game."

## What does NOT change (risk containment)

Everything that decides **what to post** stays byte-for-byte as-is. All of it is
already keyed by `match_id`, so it is already multi-game-safe:

- `_process` — the pure state machine (kickoff / goal / correction / full-time,
  half tracking, monotonic score, carried-score guard, full-time fallback).
- The ledger shape (`_fresh_ledger` / `_normalize`) and per-match `self._states`.
- `_dedup_key` (already namespaced by `match_id`).
- `_post`, `format_event`, `_wc_delete_at`, the preview CONTENT builder
  (`build_match_preview`).
- The state file format (already keyed by `match_id`).

## What DOES change

1. **The driver** — replace `_loop` (pick one) + blocking `_watch` (one match)
   with a single **active-set tick loop** (below).
2. **`_post_preview`** — refactor to accept an **injected** match row + shared
   standings table instead of doing its own `_fetch_match` + `_fetch_standings`
   (lines 204, 207). It is still driver/plumbing, not the state-machine brain,
   but it crosses the "what does NOT change" line, so it is called out explicitly
   per the review-against-the-plan rule. This is what makes N simultaneous
   previews cost ~1 standings fetch instead of N match-fetches + N standings.

## Design: active-set tick loop

Replace the serial `_loop` + blocking `_watch` with a single tick loop over an
**active set**.

### Active set
`active: dict[int, {kickoff_utc, et_day, ledger}]` — every match whose watch
window is currently open. A window **opens** at `kickoff − PRE_KICKOFF_LEAD`
(5 min) and **closes** when the ledger is `finished` OR the clock passes
`kickoff + MATCH_MAX` (2h30m, the existing safety deadline). Each entry carries
the match's own `kickoff_utc` (for `near_kickoff`/deadline) and `et_day` (for the
shared day fetch).

### Schedule source / membership (review finding 1)
Two distinct "schedules" exist; we are explicit about which:
- **API match list** (`GET /competitions/WC/matches`) — has real `id` +
  `utcDate` + `status`. This is the authority for window entry/exit.
- The static FIFA table in `wc_schedule.py` is **not** used for membership (no
  ids, no kickoff times, knockout teams TBD).

Membership is reconciled **from data we already fetch**:
- During an active window, the **per-tick day fetch already returns every game
  that day** (with `utcDate`/`status`), so new entries are derived from it at
  **zero extra cost**.
- The only gap is the **first** match of a day while the loop is idle/asleep.
  That is covered by one cheap `GET /competitions/WC/matches` during the idle
  sleep (the call today's `_loop` already makes at `:480`). **Idle lookahead ≥
  the idle sleep interval**, so a window can never open during a sleep we slept
  past (review nit).

### One tick
1. **Fetch once per distinct ET-day in the active set.** Normally 1 call; at most
   2 when a late west-coast game dates to the next UTC day (`et_date`). Each fetch
   returns every game on that day.
2. **Fan out, isolated per match (review finding 5).** For each active match,
   pull its row from the fetched payload and run the **unchanged**
   `_process(row, ledger, near_kickoff)` inside a per-match `try/except` — a
   malformed row or a failing post logs and continues; it must NEVER abort the
   tick and skip the other live games. `near_kickoff` is evaluated **per match per
   tick** from that match's own `kickoff_utc + KICKOFF_GRACE`.
3. **Persist once per tick.** A single `save_states()` after the fan-out (atomic
   temp+rename), not one write per match (review nit).
4. **Reconcile membership.** Add matches that entered their window (from the
   payload); drop matches whose ledger is `finished` or whose deadline passed.
5. **Previews.** For each newly-entered match without `preview_posted`, post its
   preview using the **already-fetched row** + a standings table fetched **once
   per tick** and shared across all previews that tick.

### Idle
When the active set is empty and no window opens within the lookahead, sleep
10 min (today's "nothing upcoming" path), doing one cheap schedule check to spot
the next day's first match. No per-tick API calls between match windows.

### Poll cadence — adaptive (decided)
One shared fetch loop, round-robining **both existing keys**. The cadence adapts
to how much is happening, so fast polling lands exactly where events occur and
the average stays well under budget even with 8 games live:

- **Hot (~2s):** for ~the first minute after a window opens (catch the real
  kickoff), and for ~30–60s right after any active match's score change or
  half-resume (follow-up goals cluster). Round-robins both keys.
- **Cool (~25s):** steady-state live play when no active match has a recent
  event. The API refreshes a score about once a minute, so ~25s samples each
  update ~2–3× — never misses a goal, and `_process`'s monotonic guard makes any
  stale tick a no-op.

Rationale for adaptive over flat-2s: the API updates ~once/min, so flat-2s
re-downloads identical data ~30× between real changes. Adaptive gets the same
goal-detection speed (it goes hot precisely when a change is likely) without
burning calls on unchanged reads — snappy AND within the 10 req/min/key free-tier
cap on the 2 keys we already have. A stray 429 when many games go hot together
yields a harmless stale read (monotonic guard) and the loop continues. No third
key required.

## Edge cases

- **Multi-day spillover:** fetch the **distinct** ET-days across the active set.
  Usually 1, at most 2.
- **Knockout days are single-match,** so the active set is ~1 and the tick
  degenerates to today's behavior — concurrency only matters in the group stage.
- **Crash / restart (review finding 2):** `load_states` already restores
  unfinished ledgers (it drops finished ones). On startup the active set =
  unfinished ledgers **intersected with currently-open windows** (same
  `kickoff + MATCH_MAX > now` math as a tick). An unfinished-but-**expired**
  ledger (crashed mid-match, restarted hours later) is **retired**, never re-added
  — mirroring `_next_kickoff`'s `:472` window filter. The API schedule fetch is
  the authority for re-entry, not the ledger.
- **Stale "live" read of an already-finished match:** finished matches leave the
  active set immediately, so a stale row can't re-trigger them.
- **A match stuck in an uncatchable state (SUSPENDED):** stays in the active set,
  ticked as a no-op, until its `MATCH_MAX` deadline — same as today.
- **Half tracking** derives from PAUSED→IN_PLAY transitions (flag-gated). A
  faster hot cadence makes catching the PAUSED read **more** likely — strictly an
  improvement.

## Testing

State-machine tests are unchanged and still pass. New tests target the **driver
only** (pure, no real I/O — feed synthetic payloads):

1. **Two games at once →** one payload fans out to both ledgers and produces both
   games' kickoff/goal/full-time posts.
2. **Window entry/exit timing →** a match enters at `kickoff − 5min`, exits on
   `finished`, and a never-finishing match exits at `kickoff + MATCH_MAX`.
3. **Shared standings →** N simultaneous previews trigger one standings fetch and
   reuse the already-fetched match rows (no per-preview match-fetch).
4. **Multi-day active set →** active matches on two ET-days trigger two day
   fetches; same-day matches trigger one.
5. **Restart rebuild →** ledgers + active set rebuild from the state file ∩
   currently-open windows; an expired unfinished ledger is retired, not re-added.
6. **Partial-failure isolation →** a match whose row/post raises does not abort the
   tick; the other active matches still post.
7. **Budget assertion →** with K active games, the tick issues at most
   `distinct_et_days` match-fetches + (≤1) standings fetch — independent of K.
8. **Adaptive cadence →** the loop is hot (~2s) after a window opens / after a
   score change, and cools (~25s) when quiescent.

## API-budget summary (adaptive, 2 keys)

| Scenario | Polls/min (both keys, round-robin) | vs 10/min/key cap |
|---|---|---|
| Idle (no window) | ~0 (one schedule check per 10-min sleep) | fine |
| Live, quiescent (cool ~25s) | ~2–3 | fine |
| Live, hot burst (~2s, ≤1 min) | ~30 split across 2 keys = ~15/min/key, bounded ≤1 min | brief, 429-tolerant |
| 8 games live, mixed | dominated by cool baseline; bursts overlap rarely | within cap; 429 = harmless stale read |
| N simultaneous previews | ~1 standings + (shared) day fetch | fine |

Concurrency is free in API terms (shared fetch); the adaptive cadence keeps the
average under cap while making kickoff/goal detection fast.

## Goals checklist (filled at PR time)
- [ ] Concurrent matches fully + symmetrically covered (preview/kickoff/goal/correction/full-time)
- [ ] No per-game API-cost increase (shared fetch; budget test #7 asserts it)
- [ ] State machine / ledger / dedup / posting untouched
- [ ] `_post_preview` injection refactor (the one intentional crossing of the containment line)
- [ ] Adaptive hot/cool cadence within the 10/min/key cap on 2 keys
- [ ] Restart intersects unfinished ledgers with open windows (expired retired)
- [ ] Per-match failure isolation in the fan-out
- [ ] Driver-level tests (1–8 above) green
- [ ] Preview consolidation explicitly DEFERRED (documented, not silently dropped)

## Review fixes folded (from the senior-eng design review)
1. **Schedule source named** — membership derived from the per-tick day payload +
   one idle schedule check; FIFA static table explicitly not used. (Finding 1)
2. **Restart edges** — active set = unfinished ledgers ∩ open windows; expired
   retired. (Finding 2)
3. **Cadence argued from API refresh rate, not "burst was awkward"** — adaptive
   hot/cool; freshness is the already-live endpoint. (Finding 3)
4. **`_post_preview` refactor called out** as a deliberate containment-line
   crossing; budget table reflects shared standings. (Finding 4)
5. **Per-match try/except** in the fan-out; a day-fetch failure skips the tick's
   fan-out but keeps the loop alive. (Finding 5)
6. **Nits:** idle lookahead ≥ sleep; one `save_states()` per tick; knockout-day
   degeneration noted; Brave vs football-data confirmed separate (no shared pool).

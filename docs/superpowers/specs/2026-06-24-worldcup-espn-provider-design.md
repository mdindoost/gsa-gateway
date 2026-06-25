# WorldCup live engine — move to a pure ESPN provider (behind a swappable seam)

**Date:** 2026-06-24
**Author:** Mohammad + Claude
**Status:** DESIGN — pending senior-eng review + Mohammad approval (expert-review hard gate)
**Touches:** `v2/integration/match_watcher.py`, `v2/integration/worldcup_tracker.py`
(`format_event`), new `v2/integration/wc_providers/` module, `v2/tests/test_match_watcher*.py`

## Goal

Replace football-data.org as the live World Cup data source with ESPN's
`site.api.espn.com` scoreboard, consumed **natively** ("pure ESPN"):

1. **Kill the ~5-min lag.** Field-observed: football-data reports kickoffs/goals ~5 min
   behind real time. ESPN is materially fresher.
2. **Richer goal posts.** ESPN supplies scorer name + minute + goal type
   (regular / own goal / penalty). Activate the *already-written-but-dormant*
   scorer/minute branch in `format_event` (`worldcup_tracker.py:370–372`).
3. **Delete complexity.** The `PAUSED→IN_PLAY` half-tracking hack in `_process`
   exists ONLY because football-data's `minute` is unreliable. ESPN gives a real
   `displayClock` + `period` + an explicit `STATUS_HALFTIME` state → the hack
   becomes removable.
4. **Stay swap-ready.** Both sources are unofficial. Wrap fetch+parse in ONE
   provider module (the same provider-isolation pattern as `njit_search` /
   Scholar `default_fetch`) so a future source swap never touches the state machine.
5. **Retire football-data** once ESPN is field-proven (the explicit end-state the
   owner asked for).

## Non-goals

- No new post TYPES. Same set (preview / kickoff / goal / correction / half-time /
  full-time). ESPN only enriches the existing ones.
- No change to the scheduler, enqueue/publish path, dedup contract, or the
  immortal-post-record invariant.

## ESPN endpoint menu (verified live + against pseudo-r/Public-ESPN-API docs, 2026-06-24)

The repo (`docs/sports/soccer.md`, `docs/response_schemas.md`) is the reference. League
slug = `fifa.world`. **No auth header. Repo's stated limit: "no official limits published,
but excessive requests may be blocked" — treat as UNKNOWN, design backoff (see Reliability).**

### DECISION (Mohammad, 2026-06-24): **scoreboard-primary**

The live source is the **scoreboard** (ONE shared call for ALL live matches — preserves the
engine's shared-fetch concurrency, ~½ the requests, lowest block risk on an unofficial API).
It already carries STRUCTURED scorer names (`details[].athletesInvolved[].displayName`),
goal kind (`ownGoal`/`penaltyKick` booleans), and a per-detail `shootout` flag — so the
GOAL post gets scorer+minute+kind without prose-parsing. Goal identity for dedup/correction
= `(matchId, athleteId, clock)`. **Corrections (VAR/disallowed):** "keep it simple" — post a
correction when a previously-announced goal cleanly disappears from a healthy `details[]`
read; stay SILENT when ambiguous (a transient/empty read never triggers a false correction).
Standings for the preview come from the dedicated `/apis/v2/.../standings` call (shared,
like today's `_fetch_standings`). The **summary** feed is demoted to an optional
enrichment/shadow candidate (its `meta.lastUpdatedAt` is not needed for this model).

Two-tier fetch — cheap shared discovery + a correctness-grade per-match feed:

| Feed | URL | Role |
|---|---|---|
| **Scoreboard** | `site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard[?dates=YYYYMMDD]` | ONE call → all matches. Discovery / active-set / kickoff times. |
| **Summary** | `site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={id}` | Per active match. The correctness feed (see below). |
| **Standings** | `site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings` (NOTE `/apis/v2/`, not `/apis/site/v2/` which returns `{}`) | Group table (also embedded IN the summary). |
| **Core plays** | `sports.core.api.espn.com/v2/sports/soccer/leagues/fifa.world/events/{id}/competitions/{id}/plays?limit=300` | Optional granular play-by-play (shadow candidate). |
| **CDN** | `cdn.espn.com/core/soccer/scoreboard?xhr=1&league=fifa.world` | "Real-time optimized" package (shadow candidate for freshest). |

### Why the **summary** feed is the live source (it resolves the review's blockers)

Verified live on event 760462. Summary carries what the scoreboard alone lacks:

```
meta.lastUpdatedAt   -> "2026-06-24T20:58:44Z"   # REAL per-match freshness stamp  → fixes B1
header.competitions[0].status.type.{state,name,completed,detail}
header.competitions[0].competitors[] {homeAway, score, team.displayName}
keyEvents[]          -> the goal/event stream, each with:
   .id        -> "49598649"   # STABLE unique event id      → fixes B2/S2 (match goals by id)
   .type.text -> "Goal" | "Own Goal" | "Penalty" | "Kickoff" | "Halftime" | ...
   .scoringPlay -> bool
   .period.number -> 1 | 2 | (3/4 ET — to verify)
   .clock.displayValue -> "29'"
   .shootout  -> bool          # explicit                    → fixes S1 (gate KO shootout)
   .wallclock -> "2026-06-24T19:28:54Z"   # per-event timestamp
   .team.id   -> credited team id  (athletesInvolved[0].displayName = scorer; see OG note)
standings.groups[].standings.entries[]  -> group table       → fixes Open Question / N1
```

The **scoreboard** also has a `competition.details[]` scoring list (with
`athletesInvolved[0].displayName` scorer names) — used only for discovery/cross-check.

### Canonical status mapping (replaces `_CANON`) — from `status.type`

Map on `state` + `completed` + a `name` allow/deny for the in-play sub-states:

| ESPN `status.type` | canonical |
|---|---|
| `state=post` AND `completed=true` | `done` |
| `state=in`, `name=STATUS_HALFTIME` / `STATUS_END_OF_PERIOD` | `paused` |
| `state=in`, `name` in {FIRST_HALF, SECOND_HALF, FIRST_EXTRA, SECOND_EXTRA, IN_PROGRESS} | `in_play` |
| `state=in`, `name` = `STATUS_SHOOTOUT` / penalties | `shootout` (NEW — NOT goal-walked) |
| `state=pre` | scheduled (uncatchable) |
| postponed / abandoned / suspended / unknown | None → ignored (as today) |

`shootout` is a new canonical state (review S1): in it, score changes are NOT walked up
as goals. The full `name` enum is enumerated from the repo, not guessed.

## Design

### 1. New provider seam — `v2/integration/wc_providers/`

```
wc_providers/
  __init__.py          get_provider(name) -> Provider     # "espn" (default) | "football_data"
  base.py              Provider protocol: fetch_day(et_day)->list[NormMatch],
                                          fetch_schedule()->list[NormMatch],
                                          fetch_standings()->dict[group,rows]
  espn.py              EspnProvider  — the new pure-ESPN impl
  football_data.py     (optional) thin wrapper over today's calls, for fallback/retire window
  normalize.py         NormMatch dataclass + ESPN->NormMatch + goal extraction
```

**`NormMatch`** — the single internal shape the state machine consumes (provider-agnostic):

```python
@dataclass
class NormMatch:
    id: int
    utc_date: str                 # "2026-06-24T19:00Z"
    state: str | None             # canonical: in_play | paused | done | None
    home: TeamRef; away: TeamRef  # name, abbr
    score: tuple[int, int]        # (home, away)
    minute: str | None            # displayClock, "67'"
    goals: list[GoalEvent]        # ordered scoring plays (scorer, minute, team, kind)
    last_updated: str | None      # freshness stamp for monotonic/correction logic
    group: str | None             # None from ESPN scoreboard (see Open Question)
```

Fetch flow: scoreboard (1 call) feeds discovery/active-set; each active match then gets
ONE `summary?event={id}` call/tick → `NormMatch`. `_parse`/`_read_meta`/`_with_score`
are deleted — the provider returns `NormMatch` already parsed. Standings come embedded in
the summary (no separate call).

### 2. State machine — `_process` becomes EVENT-DRIVEN (review B1/B2 rewrite)

The football-data design walked the SCORE up and inferred goals; the VAR-correction
guard keyed off `lastUpdated` deltas. ESPN gives us **stable goal-event ids**, so we
flip to an id-diff model — a different, more robust algorithm (NOT "the same logic stays"):

- Track the set of **announced keyEvent ids** per match (in the ledger).
- Each tick: `new_goals = scoring keyEvents whose id ∉ announced` → post each (in
  `wallclock`/sequence order, so home/away interleave is correct — fixes S2).
- A **disallowed goal** = an id we announced that is NO LONGER in keyEvents on a
  **fresher** read (`meta.lastUpdatedAt` strictly newer than the read that added it) →
  post a correction. `lastUpdatedAt` is the real freshness stamp B1 needed.
- Score line is rendered from the live `header` score, cross-checked against the
  counted goal ids (a transient empty/stale read can't lower it — same monotonic spirit).
- **Half label** from `keyEvents[].period.number` directly; `STATUS_HALFTIME` → optional
  standalone half-time post. **ET label stays a flagged fallback** (period 3/4 unverified
  on a real ET match — review S3; keep the "Extra Time" catch-all, don't claim certainty).
- **Shootout** (canonical `shootout`): keyEvents with `shootout=true` are NOT posted as
  goals — only the final result (review S1).

### 2a. Goal enrichment + own-goal labeling (review B2)

`normalize.py` resolves `team.id → displayName` (via the competitors list) BEFORE it
reaches `format_event` — `flag()` keys on NAME, not id (review caught: `flag(team_id)`
would always fall back to ⚽). Own goal: ESPN credits `team.id` to the **beneficiary**,
but `athletesInvolved[0]` is the **opponent's** player. Render so the credited side and
the (OG) scorer aren't conflated, e.g. `🥅 GOAL! 🇧🇦 Bosnia — Abunada (OG) 34'`
(credited flag = beneficiary; scorer tagged OG). Penalty → `(pen)`. Exact rendering
fixed by fixture test.

### 3. `format_event` — activate the dormant branch

No new code shape needed: the `if ev.get("scorer")` branch already renders
`🥅 GOAL! 🇧🇷 Vargas 46'`. We just start populating `scorer`/`minute`/`team`.
Own-goal / penalty get a small label suffix (`(OG)` / `(pen)`).

### 4. Rollout — shadow first, then flip, then retire

- `WC_PROVIDER=espn|football_data` env flag (default stays `football_data` until proven).
- **Shadow mode** (`WC_SHADOW_PROVIDER=espn`): run ESPN read-only alongside the live
  football-data engine; log per-tick agreement + which detects each event first +
  latency delta. Posts NOTHING. (Same playbook as the Kavosh router shadow run.)
- Flip `WC_PROVIDER=espn` after a shadow window shows ESPN ≥ as accurate and faster.
- **Retire football-data** (owner's stated end-state): delete the football_data
  provider + `FOOTBALL_API_KEY`, once ESPN has run clean through a full matchday.

## Open question — RESOLVED by the repo

Preview group table: **the summary feed embeds `standings.groups[]`** (verified). The
preview builds its table from the summary we already fetch for the live match — no
football-data, no extra call, no deferral. The standalone `/apis/v2/.../standings`
endpoint is the fallback if a pre-match summary lacks a populated table.

## Reliability — backoff (review S5; mirrors the repo's own client)

The repo's client uses exponential backoff (tenacity), per-request timeout, a real
`User-Agent`, and treats **429 → a hard rate-limit error** (no key round-robin exists for
ESPN). We adopt the same, reusing the block-aware backoff already built for
`scholar_discovery`:
- 429/403 → exponential backoff; on sustained block, a circuit-breaker drops cadence to
  COOL or pauses the match (degrade silently — never crash the loop).
- Per-request `User-Agent` = the project URL UA (per `feedback_outbound_personal_data`).
- Cost control: summary is per-active-match, but active matches are typically 1–2
  (≤ a few in group finales). HOT 2s × 2 matches = ~1 req/s — keep football-data wired
  through the shadow+flip window as an instant fallback if ESPN blocks.

## Test plan (TDD)

- `normalize.py`: ESPN fixture JSON (captured 2026-06-24, 6 matches incl. FT/HT/pre) →
  asserts NormMatch fields, status mapping for all 6, goal extraction (incl. own goal).
- `_process` with NormMatch inputs: kickoff / single goal / multi-goal walk-up /
  correction / full-time / half label from `period` — port the existing 63 tests to
  feed NormMatch instead of football-data dicts.
- Goal-enrichment: scorer+minute attached; missing-details fallback to team-only.
- Shadow logger: agreement + latency record shape.

## Senior-eng review (2026-06-24) — findings & resolution

| # | Finding | Resolution |
|---|---|---|
| **B1** | No ESPN freshness stamp for the correction guard | `summary.meta.lastUpdatedAt` is a real per-match stamp; correction model rewritten id-diff (§2). |
| **B2** | `flag()` needs a NAME, ESPN gives team id; OG mislabel | normalize.py resolves id→name; OG/pen rendering specced (§2a). |
| **S1** | KO shootout would spam "GOAL!" | new `shootout` canonical state via `keyEvents[].shootout`; not goal-walked. |
| **S2** | Two-goals-one-tick interleave | post by keyEvent id in wallclock order, not score-walk (§2). |
| **S3** | ET half-label regression | period-based label, but ET (period 3/4) kept as flagged unverified fallback. |
| **S4** | Formatter still assumes football-data dict shape | **OPEN — build decision:** adapt NormMatch→legacy dict at `_post`, OR rewrite `format_event`/`_dedup_key`/`format_standings` + update the "63 tests port cleanly" claim. Resolve in TDD step 1. |
| **S5** | No backoff for unofficial API | Reliability section added (exp backoff, 429 circuit-breaker, UA). |
| **N1** | Preview group table | summary embeds `standings.groups[]` — resolved, not deferred. |
| **N4** | Shadow agreement metric undefined | join shadow vs live by `utc_date`+team names, event-equality = type+scoreline; specced in Rollout. |

One item (S4) is a deliberate build-time decision, not a design hole — flagged loudly.

## Goals checklist (shipped/deferred — per review-against-plan rule)

- [x] G1 ESPN provider (scoreboard-primary discovery+live) + NormMatch + status map
- [x] G2 event-driven `_process` (goal-identity diff, shootout state). **Half-label derived
      from the goal MINUTE** (scoreboard feed has no `period`; minute is an exact period
      indicator — ≤45 First, ≤90 Second, beyond → Extra Time). ET unverified, safe-fallback.
- [x] G3 goal enrichment (scorer/minute/kind, OG/pen) + `format_event` activation
- [x] G4 provider seam (`EspnMatchWatcher` + `make_watcher`) + `WC_PROVIDER` flag + backoff
- [x] G5 shadow comparison tool (`scripts/wc_shadow_compare.py`, read-only A/B + latency)
- [x] G6 test suite green (49 ESPN tests; S4 resolved via `_adapter` dict shim)
- [ ] **G7 DEFERRED + FLAGGED:** preview shows matchup + kickoff context but NO group table.
      Scoreboard-primary carries no group letter; the table needs the standings endpoint —
      the flagged follow-up. (Previews still post; only the table is absent.)
- [ ] DEFERRED: retire football-data — AFTER a clean field-proven matchday (kept as the
      `WC_PROVIDER=football_data` kill-switch until then).
- [ ] KNOWN-RISK (monitor first matchday): goal identity includes `minute`; if ESPN revises a
      goal's displayed minute it could spurious-correct + re-post. Accepted ("keep it simple").
      If observed live → switch identity to `(match, athlete, seq)`.

## Two senior-eng reviews + final wiring review — all folded (2026-06-24)

Design review → scoreboard-primary decision + B1/B2/S1/S2/S3 resolved. Final LIVE-WIRING
review: verified the subclass/base seam, ledger set/JSON round-trip, mid-match-deploy silent
baseline, blocked-tick (no retire/no missed full-time), kill-switch — all SAFE. Two
should-fixes folded before merge: **#1** half-label (was hardcoded "First Half" → now
minute-derived, G2), **#2** idle discovery now spans today+tomorrow ET (no ET-midnight miss).
Verdict: SAFE TO DEPLOY.
```

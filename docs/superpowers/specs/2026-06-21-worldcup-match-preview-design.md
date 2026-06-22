# World Cup — Pre-Match Preview Post (design)

**Date:** 2026-06-21
**Owner:** Mohammad Dindoost
**Status:** **rev 3 — SHIPPED into MatchWatcher.** Scope narrowed by owner during build to:
matchup + kickoff/group context + **group table** only (H2H/squads/coaches/venue/referee cut —
the free API's H2H is unreliable and the rest added noise). Engine corrected: the live poller is
**`MatchWatcher`** (not the dormant `WorldCupRunner`/`worldcup_tracker.check_matches`), so the
preview was (re)built there. Fires ~5 min before kickoff (`PRE_KICKOFF_LEAD`). See §11 addendum.

> **rev 1/2 history (below) targeted `WorldCupRunner` — that engine is dormant; those edits were
> reverted to `eab3a38`. The reusable `match_preview.build_match_preview` formatter was kept.**
**Branch:** `worktree-worldcup-match-preview` (isolated worktree; not main).
**Related:** `v2/integration/worldcup_runner.py`, `worldcup_tracker.py`, `wc_schedule.py`,
`bot/services/worldcup_embeds.py`, the WC live-tracker work.

## 1. Goal

Post a single **pre-match preview** ~90 min before each World Cup kickoff, adding the two
ingredients the live tracker collects nothing from today — **squads (counts)** and **coaches** —
plus a **head-to-head** line, on top of context we already render (kickoff time, group, matchday,
venue, referee, live group table). Telegram/GroupMe-safe (no markdown tables, no code fences),
one combined post → Discord + Telegram via the existing connector registry.

**Non-goals:** odds (locked on free tier), predicted lineups/formations (not in the feed),
player "star/key" highlighting (no API flag → would be fabrication; explicitly cut per owner).

## 2. Confirmed data availability (live API, verified 2026-06-21)

| Ingredient | Endpoint | Status |
|---|---|---|
| Coaches | `/competitions/WC/teams` → `coach.name` | ✅ present (e.g. Bazeley / Hossam Hassan) |
| Squads (26) | same → `squad[]` `{name,position}` | ✅ present; positions `Goalkeeper/Defence/Midfield/Offence` |
| Referee | `/matches/{id}` → `referees[0]` | ✅ present (name + nationality) |
| Standings | `/competitions/WC/standings` | ✅ already consumed (`fetch_standings`) |
| Head-to-head | `/matches/{id}/head2head` | ⚠️ `aggregates` when prior meetings exist; **empty `{}`** otherwise (e.g. NZL–EGY) |
| Venue | (API `venue=null`) | use existing FIFA table `wc_schedule.venue_for` |
| Odds / lineups | — | ❌ not available — out of scope |

## 3. Post layout (approved — real data shown)

```
🔮 MATCH PREVIEW

🇳🇿 New Zealand vs 🇪🇬 Egypt
8:00 PM ET · Group G · Matchday 2
📍 Kansas City
👤 Ref: Omar Mohamed Al Ali (UAE)

📊 Group G so far
1. Iran        2 pts  (W0 D2 L0)
...

🤝 Head-to-head
First competitive meeting at a World Cup.

🇳🇿 New Zealand — Coach: Darren Bazeley
26 players · GK 3 · DEF 9 · MID 7 · FWD 7

🇪🇬 Egypt — Coach: Hossam Hassan
26 players · GK 4 · DEF 8 · MID 9 · FWD 5
```

Every rendered line traces to a real field. Optional lines are **omitted, never faked**, when the
source is missing (no coach → drop the "Coach:" clause; no venue → drop 📍; no referee → drop 👤).

## 4. Honest-partial rules (anti-fabrication — first-class)

- **H2H empty** (`aggregates == {}` / `numberOfMatches in (None,0)`) → render the literal
  `"First competitive meeting at a World Cup."` Never invent past results.
- **H2H present** → render only from real counts, oriented to THIS match's home/away:
  `"Played {n} · {home} {hw}–{aw} {away} · {d} draw(s)"` where `hw/aw/d` come from
  `aggregates.homeTeam.wins / awayTeam.wins / homeTeam.draws`.
  **(S1) Orientation is an assumption that MUST be verified, not assumed.** football-data v4
  orients `aggregates.homeTeam` to the *queried fixture's* home side — but we prove it at runtime:
  if `aggregates.homeTeam.id != match.homeTeam.id`, swap so `hw/aw` map to the real home/away
  (and if neither id matches, treat as empty → the "first meeting" line, never a guessed orientation).
  A unit test pins a real non-empty payload and asserts the swap logic. (`draws` is symmetric, so
  `homeTeam.draws == awayTeam.draws`; either is fine.)
- **Squad counts** are factual (`len(squad)` and per-position tally). Unknown/`None` positions
  bucket as "Other" and are shown only if non-zero; total always equals `len(squad)`.
- **No "key/star players"** — cut by decision; the API has no captain/star flag.

## 5. Architecture — two phases (isolation-first)

The parallel WC agent is editing `worldcup_tracker.py`, `wc_schedule.py`, `match_watcher.py`,
`worldcup_posts.py`, `post_worldcup_fact.py`. To avoid a merge conflict, the build is split:

### Phase A — isolated formatter (build now; touches NO shared files)
**New file `v2/integration/match_preview.py`** — a pure function, no I/O:

```python
def build_match_preview(
    match: dict,                 # the fixture (homeTeam/awayTeam/utcDate/group/matchday/referees)
    home_team: dict | None,      # /teams entry for home (squad[], coach) — may be None
    away_team: dict | None,      # /teams entry for away
    h2h: dict | None,            # /matches/{id}/head2head payload — may be {} or None
    standings_rows: list[dict],  # group table rows (reuse fetch_standings()[group])
    venue: str | None,           # from wc_schedule.venue_for (passed in — keeps this pure)
    kickoff_et: str,             # pre-formatted "8:00 PM ET" (caller owns tz; ET like all WC posts)
) -> str: ...
```

- Reuses `team_label` and `format_standings` from `worldcup_tracker` (import only, no edits).
- Returns a ready-to-post string. Fully unit-testable with fixtures — **no network**.
- Helper `_squad_summary(team) -> str` (counts by position) and `_h2h_line(h2h, home, away) -> str`
  (the honest-partial branch) live here.

### Phase B — wire-in (build AFTER the parallel agent lands; touches shared files)
1. **Tracker reads** (`worldcup_tracker.py`): add `fetch_teams() -> dict[str, dict]`
   (name→team, from `/competitions/WC/teams`) and `fetch_h2h(match_id) -> dict`, both mirroring
   `fetch_standings` (round-robin keys, `{}` on failure, never raise).
   - **(B3) `fetch_teams` memo:** memoize on the tracker instance, but **cache only a successful,
     non-empty result** (a `{}`/partial from a flaky tick is NOT cached — the next preview retries).
     One `/teams` call per process once it succeeds; never a permanent empty-squad degrade.
2. **Preview fetch + trigger** — the T-90 fire **cannot ride on `get_todays_matches()`**:
   - **(B1)** `get_todays_matches()` filters to the host's *local (ET) calendar day*, but a US-evening
     kickoff has `utcDate` already rolled to the *next* UTC day — so 90 min pre-kickoff the match is
     not in that set and the preview would never fire. Add **`upcoming_for_preview()`** that fetches a
     **±1-day UTC window** (`/competitions/WC/matches?dateFrom=&dateTo=`), the SAME proven pattern
     `daily_fixtures.fetch_fixtures` uses, and keeps `status in (TIMED, SCHEDULED)`.
     **Exact bounds:** query `[today, today+1]` UTC (the daily_fixtures form) — this always contains
     a match whose kickoff is within `LEAD_MIN` of `now` even when its `utcDate` rolled to the next
     UTC day. (Don't write "±1-day" in code; use the concrete `dateFrom=today, dateTo=today+1`.)
   - **(B2)** For each such match, fire a `{"type":"preview","match":match}` event iff
     `kickoff - LEAD_MIN <= now < kickoff` and `not preview_announced`, where
     `kickoff = datetime.fromisoformat(match["utcDate"].replace("Z","+00:00"))` parsed as
     **aware UTC** (the `.replace` matches `daily_fixtures._kickoff_et`; bare `fromisoformat` on the
     `Z` suffix is only safe on 3.11+ — use the replace to be explicit) and
     `now = datetime.now(timezone.utc)` (no naive/aware mixing). The `now < kickoff` upper bound means
     a bot that was down through the whole window simply **skips** the preview (a preview after kickoff
     is wrong, not better-than-nothing). Window default `FOOTBALL_PREVIEW_LEAD_MIN=90`.
   - **(S4)** Add `preview_announced: bool = False` to `MatchState` AND an explicit
     `preview_announced=f.get("preview_announced", False)` line in `load_state` (a missing `.get`
     would `KeyError` → the `except` wipes ALL state and re-fires every announced event). The flag is
     the cheap in-process guard; the durable once-guarantee is the persisted `posts` dedup row (below).
3. **Compose+enqueue** (`worldcup_runner._loop_once`): on a `preview` event, lazily fetch
   `fetch_teams()` + `fetch_h2h(id)` + `fetch_standings()[group]`, call `build_match_preview`,
   enqueue a `PostDraft(type="worldcup", dedup_key=f"{id}:preview", …)` with the channels set the
   **same way the runner already does it** — `[c for c in platform_channels() if c in self.allowed]`
   (registry-validated), not a bare `platform_channels()`.
   This ⇒ the post fans out to **Discord + Telegram + GroupMe** (GroupMe
   gated by `GROUPME_BOT_ID`) through the ConnectorRegistry — the same lane every WC post uses; no
   per-platform code. The dedup row is the durable "post once per match" backstop (survives restart).
   Failure-isolated: any sub-fetch error degrades to a shorter preview (or skips it) — never kills the
   tick, mirroring the existing kickoff-standings try/except.
4. **Toggle**: `FOOTBALL_PREVIEW_ENABLED` (default `true`); off → no preview events emitted.

## 5.5 Posting — all three platforms, one draft

The preview is **not** sent per-platform. It enqueues ONE `PostDraft` with
`channels=platform_channels()` and the publisher fans it out via the `ConnectorRegistry`:
- `platform_channels()` (`v2/core/publishing/sources.py:41`) → `["discord","telegram"]`, plus
  `"groupme"` when `GROUPME_BOT_ID` is set — so the preview reaches **Discord + Telegram + GroupMe**
  automatically, identical to every existing WC kickoff/goal/standings post. No new send code.
- **Channel-safety is a hard constraint *because* of GroupMe:** GroupMe is plain text — it strips
  markdown and cannot render tables or code fences. So the body is plain numbered lines + ` · `
  separators (bold `**` is acceptable — GroupMe simply drops it, Discord/Telegram show it), exactly
  like `format_standings`. The Phase A channel-safety test asserts no code fences / no `|`-tables.

## 6. Config (new env, all optional)

| Var | Default | Meaning |
|---|---|---|
| `FOOTBALL_PREVIEW_ENABLED` | `true` | master on/off for previews |
| `FOOTBALL_PREVIEW_LEAD_MIN` | `90` | minutes before kickoff to post |

## 7. Rate-limit budget

Per match, once: `+1 /teams`, `+1 /head2head`, standings already polled. Negligible vs the 60s live
cadence and the N-key round-robin budget. `fetch_teams` is memoized for the process lifetime (squads
don't change mid-tournament) → exactly one `/teams` call per restart — **but only a successful,
non-empty response is cached (B3)**; a flaky `{}` is not memoized so the next preview retries.

## 8. Testing (TDD)

Phase A unit tests (`v2/tests/test_match_preview.py`), pure, no network:
- full preview renders all blocks (NZL–EGY fixture data);
- H2H empty → "First competitive meeting" line;
- **H2H present, aligned orientation** (`aggregates.homeTeam.id == match.homeTeam.id`) → correct W–D–L;
- **H2H present, REVERSED orientation** (ids swapped) → swap logic still maps W–D–L to real home/away (S1);
- missing coach / venue / referee / matchday → those lines omitted, rest intact;
- squad counts sum to `len(squad)`; `null`/unknown `position` → "Other" bucket, only shown if non-zero;
- output contains no code fences / markdown tables (channel-safety / GroupMe assertion).

Phase B: extend `v2/tests/test_worldcup.py`:
- **(B1) UTC-boundary trigger:** a match whose `utcDate` is the *next* UTC day vs the host's ET
  `today()` still fires its preview at T-90 (proves `upcoming_for_preview`'s ±1-day window, not
  `get_todays_matches`);
- preview fires exactly once in-window; **not before** T-90; **not after kickoff** (`now < kickoff` bound);
- **(S4)** loading a pre-existing flag-less `worldcup_state.json` defaults `preview_announced=False`
  without wiping state;
- **(B3)** a `{}` from `fetch_teams` is not memoized (next call retries);
- toggle off suppresses previews; any sub-fetch failure degrades gracefully (tick survives).

Add the verifying questions/checks to the WC eval where applicable (grow-the-suite rule).

## 9. Rollout

DB-untouched, code-only → needs `scripts/restart.sh`. Ship Phase A first (dead code, no trigger),
then Phase B once the parallel agent merges. Watch one real fixture, confirm single post at T-90.

## 10. Goals checklist (shipped / deferred)

- [x] Squads (counts by position) in preview — **core goal** (`_squad_summary`)
- [x] Coaches in preview — **core goal** (`_team_block`)
- [x] Head-to-head with honest-partial empty handling + runtime orientation check — **core goal** (`_h2h_line`)
- [x] Reuse existing standings/venue/referee/kickoff context (`format_standings`, `venue_for`, `_kickoff_et`, `referees[0]`)
- [x] One draft → Discord + Telegram + GroupMe via `platform_channels()`; GroupMe-safe rendering (no fences/tables)
- [x] Reliable T-90 trigger on a 2-day UTC window (`upcoming_for_preview`), `now < kickoff` bound + aware-UTC parse (`check_previews`)
- [x] Isolated pure formatter (Phase A `match_preview.py`), no shared-file edits in that module
- [x] Phase B wiring BUILT in this branch (tracker fetches + trigger + runner). **Merge note:** Phase B
      edits `worldcup_tracker.py` + `worldcup_runner.py`; land AFTER the parallel `feat/unify-modes`
      agent merges, expect a small manual merge there. NOT silently dropped — built, flagged for sequencing.
- [x] Toggle (`FOOTBALL_PREVIEW_ENABLED`) + lead-time (`FOOTBALL_PREVIEW_LEAD_MIN`) config
- [x] Tests: 16 unit (formatter) + 12 trigger/wiring; zero regressions (failing set identical to baseline)
- [x] **Cut, on purpose (loudly):** odds (locked), lineups (not in feed), "key players" (no API flag → fabrication)
- [ ] **Deferred:** referee nationality renders FULL ("United Arab Emirates") not "(UAE)" — no
      abbreviation mapping built (factual, just longer than the mockup). Flagged, not silently changed.
- [ ] **Deferred:** Phase B scheduler does not pre-`scheduled_for` the post at exactly T-90; it posts on
      the first poll tick inside the window (≤ poll interval late). Acceptable; note for future tightening.

## 11. Addendum (rev 3) — final implementation in MatchWatcher

**Engine:** `bot/main.py` runs `MatchWatcher` ("replaces the constant WorldCupRunner"). It already
idles until `PRE_KICKOFF_LEAD = 5 min` before kickoff — the natural home for a pre-match preview.

**What shipped:**
- `v2/integration/match_preview.py` — pure `build_match_preview(match, standings_rows, kickoff_et)`:
  `⏳ MATCH PREVIEW` + `{home} vs {away}` + `kickoff · group · matchday` + the group table
  (`format_standings`). Channel-safe (no fences/tables), GroupMe-stripped bold.
- `v2/integration/match_watcher.py`:
  - `_fetch_standings(key)` — `{GROUP_X: rows}`, group key normalized `Group H` → `GROUP_H`.
  - `_post_preview(match_id, et_day, kickoff_utc)` — gated by `FOOTBALL_PREVIEW_ENABLED` (default on),
    fetches the fixture + standings, enqueues one post (`dedup_key={id}:preview`, `event_type=preview`)
    to Discord+Telegram+GroupMe via `platform_channels()`. Best-effort (False if disabled/unavailable).
  - `preview_posted` ledger flag (in `_fresh_ledger`/`_normalize`, persisted) — fire-once; the
    durable `posts` dedup row is the cross-restart guard. Wired into `_watch` after the pre-kickoff sleep.

**Cut (loudly, on purpose):** head-to-head (free API returns empty/inconsistent — see the NZL–Egypt
and Argentina–Austria findings), squads, coaches, venue, referee, odds, lineups.

**Reverted:** the rev-1/2 `WorldCupRunner`/`worldcup_tracker`/`test_worldcup` edits (dormant engine).

**Config:** `FOOTBALL_PREVIEW_ENABLED` (default `true`). Lead time follows MatchWatcher's existing
`PRE_KICKOFF_LEAD` (5 min) — no separate env.

**Tests:** `_fetch_standings` key-norm, `_post_preview` (post-once + dedup, toggle off, skip when
fixture unavailable), `preview_posted` ledger default/roundtrip, + the `match_preview` formatter suite.
Full-suite failing set identical to baseline (zero regressions).

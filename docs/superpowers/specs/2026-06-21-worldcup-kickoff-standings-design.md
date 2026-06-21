# World Cup — Kick‑off Group‑Standings Post (Design)

**Date:** 2026-06-21
**Author:** Mohammad Dindoost (owner) + Claude
**Status:** Design — pending expert review + owner approval (EXPERT-REVIEW HARD GATE)
**Area:** `v2/integration/worldcup_*` (World Cup live-post lane)

## Goal

When a **group-stage** match kicks off, publish **one extra post** showing **that
match's group table**, delivered **immediately before** the existing `KICK-OFF!`
post, to **both** channels (Discord + Telegram). The existing goal / half-time /
full-time / kick-off posts are **not changed**.

Example sequence (real live data, Group H):

```
📊 Standings — Group H
# Team               P W D L  GD Pts
1 Spain              2 1 1 0  +4   4
2 Cape Verde Island  2 1 1 0  +1   4
3 Uruguay            2 0 1 1  -1   1
4 Saudi Arabia       2 0 1 1  -4   1
```
then the unchanged:
```
⚽ KICK-OFF!
🇪🇸 Spain vs 🇨🇻 Cape Verde Islands
The match is underway! 🌍
_Group Stage · Group H_
```

## Decisions (owner, 2026-06-21)

| Question | Decision |
| --- | --- |
| Which standings on kick-off? | **Only that match's group table** |
| Order vs the KICK-OFF post | **Standings first, then KICK-OFF** |
| Knockout rounds (no groups) | **Skip — group stage only** |
| Channels | Both (same as the kick-off post) |
| Format | Plain markdown code-block table (cross-platform; **not** a Discord embed) |
| Data source | football-data.org `/competitions/WC/standings` via the existing `WorldCupTracker` multi-key path |

## Non-goals (YAGNI)

- No knockout bracket / overall standings post (deferred; the `/standings`
  endpoint returns nothing once the bracket starts — separate feature if wanted).
- No flag emoji inside the table (double-width glyphs break monospace alignment).
- No change to the `/worldcup standings` slash command or the orphaned
  `bot/services/football_client.py` (out of scope — see "Notes" below).
- No change to goal/scoring/half-time/full-time post logic.

## Architecture

The live World Cup post lane is: `WorldCupRunner._loop_once` polls
`WorldCupTracker.check_matches()` → for each event, `format_event(ev)` →
`enqueue_post(...)` as a `posts` row → `Publisher.publish_due` delivers it through
the ConnectorRegistry (Discord + Telegram).

This feature adds **three additive pieces** and modifies **only the kick-off
branch** of `_loop_once`.

### 1. `WorldCupTracker.fetch_standings()` — new method (additive)

```python
async def fetch_standings(self) -> dict[str, list[dict]]:
    """Return {group_name: [table_rows]} for the WC group stage. {} on failure."""
    data = await self._get("/competitions/WC/standings")
    out = {}
    for block in data.get("standings", []):
        g = block.get("group")
        if g:                       # group-stage blocks only
            out[g] = block.get("table", [])
    return out
```

- Reuses the existing `_get()` — round-robins the comma-separated keys and already
  returns `{}` on any HTTP/network failure (never raises). This is the **same
  working machinery** that powers live scores; it does **not** touch
  `bot/services/football_client.py` (which is unsplit + unwired — see Notes).

### 2. `format_standings(group_name, rows) -> str` — new pure function (additive)

Lives next to `format_event` in `worldcup_tracker.py`. Pure (no I/O) → unit-tested
directly. Renders:

```
📊 **Standings — {group_name}**
```<code block>
# Team               P W D L  GD Pts
1 Spain              2 1 1 0  +4   4
...
```<code block>
```

- Columns: position, team name (truncated to a fixed width), `playedGames`, `won`,
  `draw`, `lost`, `goalDifference` (signed), `points`.
- Wrapped in a ``` fenced code block so the monospace columns align on both Discord
  and Telegram.
- Defensive: missing numeric fields render as `0`; an empty `rows` returns `""`
  (caller then skips the post).

### 3. Kick-off branch in `WorldCupRunner._loop_once` (the only modification)

Current loop enqueues one post per event. New behavior — **only** for a `kickoff`
event whose match has a non-empty `group`:

1. `groups = await self.tracker.fetch_standings()` (wrapped in try/except).
2. `table = groups.get(match["group"])`.
3. If `table`: enqueue a **standings** `PostDraft` (dedup key
   `f"{match_id}:standings"`), ordered to deliver **before** the kick-off post
   (see "Delivery ordering").
4. Then enqueue the kick-off post exactly as today.

All other event types (goal, half-time, second-half, full-time, correction) and
**knockout kick-offs** (empty `group`) are unchanged — one post each, no standings.

### Delivery ordering (must be explicit)

`Publisher.publish_due` (`v2/core/publishing/publisher.py:114`) orders due posts by
`scheduled_for IS NULL DESC, scheduled_for`. World Cup posts have
`scheduled_for = NULL`, so two posts enqueued in one tick are **tied**, and SQLite
does **not** guarantee insertion order on a tie. "Standings before kick-off" must
therefore be made explicit. **Two candidate mechanisms — reviewer to choose:**

- **(A) Preferred — deterministic tiebreaker:** append `, id` to the
  `publish_due` ORDER BY. This makes *all* simultaneously-due posts deliver in
  insertion order (rowid), a correctness improvement with no behavior change for
  scheduled posts. Touches shared publishing infra → needs senior-eng sign-off.
- **(B) Isolated fallback:** give the standings post an explicit `scheduled_for`
  one second earlier than the kick-off post (or set both, standings < kickoff), so
  the existing `scheduled_for` sort delivers standings first. No shared-infra
  change, fully contained in the runner, but relies on a 1s skew.

The build will implement **(A)** if the reviewer approves the shared-query change;
otherwise **(B)**. Either way an integration test asserts the standings row is
delivered before the kick-off row.

### Failure isolation (hard requirement)

The standings fetch + format + enqueue is wrapped so that **any** failure (API
down, group missing, empty table, formatter error) is logged and skipped, and the
**kick-off post is still enqueued exactly as today**. A standings problem can never
break, delay, or duplicate the existing posts. `_loop_once` already runs under a
per-tick try/except (`_loop`), but the standings work gets its own inner guard so a
failure doesn't even skip the kick-off post within the same event.

### Toggle

Gated behind `FOOTBALL_KICKOFF_STANDINGS` (default `true`). Set `=false` in `.env`
to disable without a code change. Read once in `WorldCupRunner.__init__`.

## Data flow

```
poll tick
  └─ tracker.check_matches() → [events...]
       └─ for ev in events:
            if ev.type == "kickoff" and ev.match.group and standings_enabled:
                try:
                    groups = await tracker.fetch_standings()
                    table  = groups.get(ev.match.group)
                    if table:
                        enqueue( standings post )   # dedup {id}:standings, ordered first
                except Exception: log + continue     # kick-off still posts
            enqueue( format_event(ev) )              # unchanged
```

## Testing (TDD — extend `v2/tests/test_worldcup.py`)

1. `format_standings(group, rows)` → exact expected markdown string (incl. signed
   GD, truncation, code fences).
2. `format_standings("X", [])` → `""`.
3. Kick-off event **with** group → `_loop_once` enqueues **2** posts; standings has
   dedup `"{id}:standings"`; standings is ordered before the kick-off post.
4. Kick-off event **without** group (knockout) → enqueues **only** the kick-off
   post (no standings).
5. `fetch_standings` raises / returns `{}` → kick-off post **still** enqueued
   (resilience).
6. `FOOTBALL_KICKOFF_STANDINGS=false` → no standings post on kick-off.
7. Goal / half-time events → unchanged, one post each (regression guard).

`fetch_standings` is monkeypatched in tests (no live network). One optional
live-smoke check (manual) against the real endpoint, already validated during
design (all 12 groups returned on the free tier).

## Notes / out-of-scope findings (for the record)

- `bot/services/football_client.py` is **unwired** (`self.bot.football_client`
  is never set) and does **not** split the comma-separated keys, so the
  `/worldcup standings` slash command currently can't work. This feature
  deliberately bypasses it by using `WorldCupTracker`. Fixing/retiring
  `FootballClient` + the slash command is a **separate** task, not in this scope.

## Goals checklist (to verify at PR close — shipped vs deferred)

- [ ] Standings post on group-stage kick-off, that group only — **shipped?**
- [ ] Delivered before the KICK-OFF post (ordering test green) — **shipped?**
- [ ] Both channels (Discord + Telegram) — **shipped?**
- [ ] Knockout kick-offs skip standings — **shipped?**
- [ ] Existing goal/scoring/kick-off posts unchanged (regression test) — **shipped?**
- [ ] Failure isolation: standings error never blocks the kick-off post — **shipped?**
- [ ] `FOOTBALL_KICKOFF_STANDINGS` toggle — **shipped?**
- [ ] Knockout bracket/overall standings post — **DEFERRED** (explicit non-goal).
- [ ] `FootballClient` / `/worldcup standings` slash-command fix — **DEFERRED** (separate task).
```

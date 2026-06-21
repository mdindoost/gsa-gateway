# World Cup — Kick‑off Group‑Standings (Single Combined Post) Design

**Date:** 2026-06-21
**Author:** Mohammad Dindoost (owner) + Claude
**Status:** Design — pending expert review + owner approval (EXPERT-REVIEW HARD GATE)
**Area:** `v2/integration/worldcup_*` (World Cup live-post lane)

## Goal

When a **group-stage** match kicks off, the existing `KICK-OFF!` post also shows
**that match's group table**, as **one combined post** (kick-off announcement +
table together), delivered to **both** channels (Discord + Telegram). Goal /
half-time / second-half / full-time posts are **not changed**.

Example combined post (real live data, Group H):

```
⚽ KICK-OFF!
🇪🇸 Spain vs 🇨🇻 Cape Verde Islands
The match is underway! 🌍
_Group Stage · Group H_

📊 Standings — Group H
# Team               P W D L  GD Pts
1 Spain              2 1 1 0  +4   4
2 Cape Verde Island  2 1 1 0  +1   4
3 Uruguay            2 0 1 1  -1   1
4 Saudi Arabia       2 0 1 1  -4   1
```

## Decisions (owner, 2026-06-21)

| Question | Decision |
| --- | --- |
| Which standings on kick-off? | **Only that match's group table** |
| Layout | **One combined post** — KICK-OFF text first, then the group table below it |
| Knockout rounds (no groups) | **Skip the table — post the normal kick-off only** |
| Channels | Both (same as the kick-off post) |
| Format | Plain markdown code-block table appended to the kick-off content (**not** a Discord embed) |
| Data source | football-data.org `/competitions/WC/standings` via the existing `WorldCupTracker` multi-key path |

> **Changed from the first draft (owner, 2026-06-21):** originally two separate
> posts (standings before kick-off). Now a **single post**. This removes the
> delivery-ordering problem entirely (no publisher change, no scheduled_for skew,
> no second `posts` row, no new dedup key).

## Non-goals (YAGNI)

- No knockout bracket / overall standings (deferred; `/standings` returns nothing
  once the bracket starts — a separate feature if wanted).
- No flag emoji inside the table (double-width glyphs break monospace alignment).
- No change to the `/worldcup standings` slash command or the orphaned
  `bot/services/football_client.py` (out of scope — see "Notes").
- No change to goal / half-time / second-half / full-time / correction posts.
- No change to the publisher / post-queue (the single-post design needs none).

## Architecture

The live World Cup post lane is: `WorldCupRunner._loop_once` polls
`WorldCupTracker.check_matches()` → for each event, `format_event(ev)` →
`enqueue_post(...)` as one `posts` row → `Publisher.publish_due` delivers it
through the ConnectorRegistry (Discord + Telegram).

This feature adds **two additive pieces** and changes **only how the kick-off
event's `content` string is built** inside `_loop_once`. No new post, no new dedup
key, no publisher change.

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
  returns `{}` on any HTTP/network failure (never raises). Same working machinery
  that powers live scores; does **not** touch `bot/services/football_client.py`.

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
- Wrapped in a fenced code block so columns align on both Discord and Telegram.
- Defensive: missing numeric fields render as `0`; empty `rows` returns `""`.

### 3. Kick-off content assembly in `WorldCupRunner._loop_once` (the only change)

Today the loop sets `content = format_event(ev)` for every event. New behavior —
**only** for a `kickoff` event whose match has a non-empty `group` and when the
toggle is on:

```python
content = format_event(ev)                 # unchanged base kick-off text
if ev["type"] == "kickoff" and match.get("group") and self.kickoff_standings:
    try:
        table = (await self.tracker.fetch_standings()).get(match["group"])
        if table:
            content = content + "\n\n" + format_standings(match["group"], table)
    except Exception:                      # noqa: BLE001
        logger.exception("WC kickoff standings failed; posting plain kick-off")
        # content stays the plain kick-off text
```

- **`format_event` is NOT modified.** Knockout kick-offs (empty `group`), a
  standings fetch failure, or an empty table all fall through to exactly today's
  kick-off post.
- The `PostDraft` is otherwise built **exactly as today** — same `type`, same
  channels, **same existing kick-off dedup key**, same metadata. There is still
  **one** post per kick-off.
- All other event types are untouched.

### Failure isolation (hard requirement)

The standings fetch + format is wrapped in its own try/except *inside* the kick-off
branch, so any failure (API down, group missing, empty table, formatter error) is
logged and the post degrades to the **plain kick-off text that ships today**. A
standings problem can never break, delay, drop, or duplicate the kick-off post.

### Toggle

Gated behind `FOOTBALL_KICKOFF_STANDINGS` (default `true`). Set `=false` in `.env`
to fall back to the plain kick-off post with no code change. Read once in
`WorldCupRunner.__init__` → `self.kickoff_standings`.

## Data flow

```
poll tick
  └─ tracker.check_matches() → [events...]
       └─ for ev in events:
            content = format_event(ev)                       # unchanged
            if ev.type == "kickoff" and ev.match.group and enabled:
                try:
                    table = (await tracker.fetch_standings()).get(group)
                    if table: content += "\n\n" + format_standings(group, table)
                except Exception: log; keep plain kick-off text
            enqueue( one PostDraft with `content` )           # one post, as today
```

## Testing (TDD — extend `v2/tests/test_worldcup.py`)

1. `format_standings(group, rows)` → exact expected markdown (signed GD, truncation,
   code fences).
2. `format_standings("X", [])` → `""`.
3. Kick-off event **with** group → `_loop_once` enqueues exactly **one** post whose
   content contains **both** the kick-off lines **and** the group table; dedup key
   equals the existing kick-off dedup key (no new/extra post).
4. Kick-off event **without** group (knockout) → exactly one post, plain kick-off
   text, no table.
5. `fetch_standings` raises / returns `{}` / group missing → exactly one post, plain
   kick-off text (resilience).
6. `FOOTBALL_KICKOFF_STANDINGS=false` → exactly one post, plain kick-off text.
7. Goal / half-time events → unchanged, one post each, no standings (regression).

`fetch_standings` is monkeypatched in tests (no live network). The live endpoint
was validated during design (all 12 groups returned on the free tier).

## Notes / out-of-scope findings (for the record)

- `bot/services/football_client.py` is **unwired** (`self.bot.football_client` is
  never set) and does **not** split the comma-separated keys, so the
  `/worldcup standings` slash command currently can't work. This feature bypasses
  it by using `WorldCupTracker`. Fixing/retiring `FootballClient` + the slash
  command is a **separate** task, not in this scope.

## Goals checklist (verify at PR close — shipped vs deferred)

- [ ] Single combined post: kick-off text + that group's table on group-stage kick-off — **shipped?**
- [ ] Both channels (Discord + Telegram) — **shipped?**
- [ ] Knockout kick-offs → plain kick-off, no table — **shipped?**
- [ ] Existing goal/scoring/half-time/full-time posts unchanged (regression test) — **shipped?**
- [ ] `format_event` unmodified; one post per kick-off, existing dedup key — **shipped?**
- [ ] Failure isolation: standings error degrades to the plain kick-off post — **shipped?**
- [ ] `FOOTBALL_KICKOFF_STANDINGS` toggle — **shipped?**
- [ ] Knockout bracket/overall standings — **DEFERRED** (explicit non-goal).
- [ ] `FootballClient` / `/worldcup standings` slash-command fix — **DEFERRED** (separate task).
```

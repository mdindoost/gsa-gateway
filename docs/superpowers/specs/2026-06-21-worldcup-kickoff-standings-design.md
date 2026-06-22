# World Cup — Kick‑off Group‑Standings (Single Combined Post) Design

**Date:** 2026-06-21
**Author:** Mohammad Dindoost (owner) + Claude
**Status:** Design — reviewed (senior-eng APPROVE-WITH-CHANGES, applied) + owner-approved direction; ready for TDD build
**Area:** `v2/integration/worldcup_*` (World Cup live-post lane)

## Goal

When a **group-stage** match kicks off, the existing `KICK-OFF!` post also shows
**that match's group table**, as **one combined post** (kick-off announcement +
table together), delivered to **every configured channel** (Discord, Telegram,
**and GroupMe**). Goal / half-time / second-half / full-time posts are **not
changed**.

Example combined post (real live data, Group H) — rendered identically across all
three platforms (bold survives on Discord/Telegram, is stripped to clean text on
GroupMe):

```
⚽ KICK-OFF!
🇪🇸 Spain vs 🇨🇻 Cape Verde Islands
The match is underway! 🌍
_Group Stage · Group H_

📊 Group H
1. Spain — 4 pts · GD +4
2. Cape Verde Islands — 4 pts · GD +1
3. Uruguay — 1 pt · GD -1
4. Saudi Arabia — 1 pt · GD -4
```

## Decisions (owner, 2026-06-21)

| Question | Decision |
| --- | --- |
| Which standings on kick-off? | **Only that match's group table** |
| Layout | **One combined post** — KICK-OFF text first, then the group table below |
| Knockout rounds (no groups) | **Skip the table — post the normal kick-off only** |
| Channels | **All configured** — Discord, Telegram, **and GroupMe** (decided by `platform_channels()`, not by this feature) |
| Format | **Monospace-free, one line per team** (NO code fences). See "Cross-platform rendering". |
| Data source | football-data.org `/competitions/WC/standings` via the existing `WorldCupTracker` multi-key path |

> **Change history (owner, 2026-06-21):**
> 1. First draft = two posts (standings before kick-off). → Now **one** combined
>    post (removes the delivery-ordering problem entirely).
> 2. First format = a ``` ```-fenced monospace grid. → **Rejected:** that grid only
>    renders on Discord. Telegram converts only bold/italic to HTML and
>    HTML-escapes the rest (`telegram_connector.py:25`), and GroupMe is plain text
>    (`groupme_connector.py:20`) — both would show **literal backticks + misaligned
>    columns**. GroupMe is a real target channel (`sources.py:37,48`, included when
>    `GROUPME_BOT_ID` is set). → Now a **monospace-free, one-line-per-team** format.

## Cross-platform rendering (the constraint that drives the format)

One `content` string is enqueued and shared by all connectors, so the format must
read well on the **weakest** renderer (GroupMe plain text). Therefore:

- **No code fences, no column alignment** — never rely on monospace.
- Only `**bold**` markup is used, and only on the group header line. It renders bold
  on Discord/Telegram and is cleanly stripped on GroupMe.
- Each team is **one self-contained line**: position, name, points, P/W/D/L, GD.
- ASCII `+`/`-` for goal difference (no unicode minus) for safety on all platforms.

Per-team line template:
```
{pos}. {team} — {pts} pt{s} · GD {+/-gd}
```
Header line: `📊 **{group_label}**` (e.g. `Group H`). `pt`/`pts` pluralized.

## Non-goals (YAGNI)

- No knockout bracket / overall standings (deferred; `/standings` returns nothing
  once the bracket starts — a separate feature if wanted).
- No flag emoji inside the table (kept on the kick-off lines only; not per row).
- No change to the `/worldcup standings` slash command or the orphaned
  `bot/services/football_client.py` (out of scope — see "Notes").
- No change to goal / half-time / second-half / full-time / correction posts.
- No change to the publisher / post-queue / connectors (the design needs none).

## Architecture

The live World Cup post lane is: `WorldCupRunner._loop_once` polls
`WorldCupTracker.check_matches()` → for each event, `format_event(ev)` →
`enqueue_post(...)` as one `posts` row → `Publisher.publish_due` delivers it
through the ConnectorRegistry (Discord + Telegram + GroupMe).

This feature adds **two additive pieces** and changes **only how the kick-off
event's `content` string is built** inside `_loop_once`. No new post, no new dedup
key, no publisher/connector change.

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
- **Schema reference:** the `block.get("group")/.get("table")` and row accessors
  match the existing, working `build_standings_embed`
  (`bot/services/worldcup_embeds.py:319-340`). The implementer copies those proven
  accessors verbatim rather than guessing field names.

### 2. `format_standings(group_name, rows) -> str` — new pure function (additive)

Lives next to `format_event` in `worldcup_tracker.py`. Pure (no I/O) → unit-tested
directly. Emits the monospace-free format above:

```
📊 **Group H**
1. Spain — 4 pts · GD +4
...
```

- Row fields: `position`, `team.name`, `points`, `goalDifference` (signed `+`/`-`).
  (Owner trimmed the line to rank · name · points · GD — P/W/D/L dropped.)
- **Defensive:** missing numeric fields render as `0`; a missing name renders as
  `"?"`; an empty `rows` returns `""` (caller then skips the append).
- **Team-name length:** truncate names longer than **28 chars** to `27 + "…"`
  (a generous cap purely to bound a pathological name; real WC team names fit).
  Alignment is irrelevant here since the format isn't a grid.
- **Length:** a 4-row group renders to ~300 chars; the combined kick-off post is
  ~450 chars — far under `enqueue_post`'s `MAX_CONTENT = 4000` hard limit
  (`sources.py:30,110`). No code cap needed.
- **Asserted invariant:** the output contains **no triple-backtick** (GroupMe-safety
  regression guard, tested).

### 3. Kick-off content assembly in `WorldCupRunner._loop_once` (the only change)

Today `content=format_event(ev)` is computed **inline inside the `PostDraft(...)`
constructor** (`worldcup_runner.py:88`). The change **hoists it to a local** above
the constructor, conditionally appends the table, and passes `content=content`:

```python
content = format_event(ev)                          # unchanged base text
match   = ev.get("match") or {}                      # already in scope (line 73)
if ev.get("type") == "kickoff" and match.get("group") and self.kickoff_standings:
    try:
        table = (await self.tracker.fetch_standings()).get(match["group"])
        if table:
            content = content + "\n\n" + format_standings(_group_label(match["group"]), table)
    except Exception:                                # noqa: BLE001
        logger.exception("WC kickoff standings failed; posting plain kick-off")
        # content stays the plain kick-off text
...
draft = PostDraft(..., content=content, ...)         # same draft, content now a local
```

- **`format_event` is NOT modified.** Knockout kick-offs (`group` falsy), a
  standings fetch failure, an empty table, or the toggle off all fall through to
  exactly today's kick-off post.
- `match.get("group")` is `"GROUP_A"` etc. for group-stage matches and falsy
  (`null`) for knockout — confirmed by `worldcup_tracker.py:299,317` and the
  persisted match state. `_group_label("GROUP_A") -> "Group A"` (reuse the existing
  `.replace("_"," ").title()` idiom from `_context`, `worldcup_tracker.py:317-318`).
- The `PostDraft` is otherwise built **exactly as today** — same `type`, channels
  (`[c for c in platform_channels() if c in self.allowed]`, which already includes
  GroupMe when configured), **same existing kick-off dedup key**
  (`worldcup_runner.py:79-85`, content-independent), metadata. Still **one** post.
- All other event types untouched.

### Failure isolation (hard requirement)

`content = format_event(ev)` is assigned **before** the try block. The standings
fetch/format/append is the only thing inside `try`. On **any** failure (API down,
`{}`, missing group, empty table, formatter error) the post degrades to the **plain
kick-off text that ships today**. No path drops, delays past `_get`'s bounded
timeout, or duplicates the kick-off post. (Note: a slow standings fetch is bounded
by `_get`'s 10s timeout × ≤3 retries and only delays that one tick's *enqueue*, not
delivery; acceptable.)

### Toggle

`FOOTBALL_KICKOFF_STANDINGS`, read once in `WorldCupRunner.__init__`:
```python
self.kickoff_standings = os.getenv("FOOTBALL_KICKOFF_STANDINGS", "true").lower() != "false"
```
Default **true** (matches the house `os.getenv(...).lower()` pattern at
`worldcup_tracker.py:112,117`). Set `=false` in `.env` to fall back to the plain
kick-off post with no code change.

## Data flow

```
poll tick
  └─ tracker.check_matches() → [events...]
       └─ for ev in events:
            content = format_event(ev)                       # unchanged
            if ev.type == "kickoff" and ev.match.group and enabled:
                try:
                    table = (await tracker.fetch_standings()).get(group)
                    if table: content += "\n\n" + format_standings(label, table)
                except Exception: log; keep plain kick-off text
            enqueue( one PostDraft with `content` )           # one post, as today
```

## Testing (TDD — extend `v2/tests/test_worldcup.py`)

1. `format_standings("Group H", rows)` → exact expected string (line format, signed
   GD, `pt`/`pts` pluralization, bold header).
2. `format_standings("X", [])` → `""`.
3. **GroupMe-safety:** `format_standings(...)` output contains **no** ```` ``` ````
   and no leading/trailing code fence (locks the cross-platform requirement).
4. Kick-off event **with** group → `_loop_once` enqueues exactly **one** post whose
   content contains **both** the kick-off lines **and** the group table; dedup key
   equals the existing kick-off dedup key (no extra/second post).
5. Kick-off event **without** group (knockout) → exactly one post, plain kick-off
   text, no table.
6. `fetch_standings` raises / returns `{}` / group missing → exactly one post, plain
   kick-off text (resilience).
7. `FOOTBALL_KICKOFF_STANDINGS=false` → exactly one post, plain kick-off text.
8. Goal / half-time events → unchanged, one post each, no standings (regression).

`fetch_standings` is monkeypatched in tests (no live network), following the
existing `_loop_once`-drives-a-post fixture pattern (`test_worldcup.py:144-203`).
The live endpoint was validated during design (all 12 groups on the free tier).

## Notes / out-of-scope findings (for the record)

- `bot/services/football_client.py` is **unwired** (`self.bot.football_client` is
  never set) and does **not** split the comma-separated keys
  (`football_client.py:23-26`), so the `/worldcup standings` slash command can't
  work today. This feature bypasses it via `WorldCupTracker`. Fixing/retiring
  `FootballClient` + the slash command is a **separate** task, not in this scope.

## Goals checklist (verify at PR close — shipped vs deferred)

- [ ] Single combined post: kick-off text + that group's table on group-stage kick-off — **shipped?**
- [ ] Renders correctly on **all three** channels (Discord, Telegram, GroupMe) — no code fences — **shipped?**
- [ ] Knockout kick-offs → plain kick-off, no table — **shipped?**
- [ ] Existing goal/scoring/half-time/full-time posts unchanged (regression test) — **shipped?**
- [ ] `format_event` unmodified; one post per kick-off, existing dedup key — **shipped?**
- [ ] Failure isolation: standings error degrades to the plain kick-off post — **shipped?**
- [ ] `FOOTBALL_KICKOFF_STANDINGS` toggle (default true) — **shipped?**
- [ ] Knockout bracket/overall standings — **DEFERRED** (explicit non-goal).
- [ ] `FootballClient` / `/worldcup standings` slash-command fix — **DEFERRED** (separate task).
```

# v2 Platform Architecture — Design Spec

**Date:** 2026-06-10
**Author:** Mohammad Dindoost (with Claude Code)
**Status:** Approved vision — phased implementation pending

---

## 1. Problem & Motivation

The current system runs as v1 with v2 "wired in alongside" it. The two coexist
in one process, and v1 still **originates** outbound messages. Concretely: the
v1 scheduler cog kept firing a daily `📅 This Week from GSA` digest (observed in
Discord on 2026-06-09 and 2026-06-10 at 10:00 AM), because `daily_digest` is
started unconditionally in `bot/services/scheduler.py:291-292` and the cog is
loaded via `bot/main.py:43`. Turning on v2 (`V2_SCHEDULER_ENABLED=true`) added a
second scheduler **next to** v1 instead of replacing it.

This is a symptom of a deeper architectural issue: **v1 hardcodes every feature
as its own cog** (a digest cog, a World Cup cog, a MathCafe cog). Every new idea
requires new code. That model does not scale to the v2 goal.

## 2. The v2 Vision

v2 is **not** "the GSA bot rewritten." It is a **generic, multi-tenant platform**
that other universities and organizations can adopt. The controlling principle:

> **Features are configuration, not code.**

A future admin at any university composes whatever they need — events, reminders,
recurring posts, even an external-data tracker like a World Cup feed — from a
**dashboard**, with no developer touching the codebase. The platform ships a
small set of **primitives**; everything else is data/config on top of them.

The dashboard + database is the single control plane. Anything that goes *out* is
defined in the DB and dispatched by the v2 scheduler through connectors. There
are **no code-side autonomous senders**.

### Test of correctness

A feature is "done right" if it can be added or changed **without a code change**.
World Cup, MathCafe, and the weekly digest must not survive as hardcoded features.
They are removed, and (where wanted) reintroduced as *configuration* on top of
generic primitives.

## 3. Transport Reality (important constraint)

v2 has **no Discord/Telegram gateway of its own**. The `v2/` tree is purely a
publishing layer: database → scheduler → connectors. Its connectors send by
borrowing the live v1 client (`DiscordConnector(client=DiscordClientAdapter(self))`
in `bot/main.py` "Wire B"; Telegram analogously via `run_telegram.py`).

Therefore "pure v2 / nothing from v1" cannot mean "delete the v1 process." It
means precisely:

> **v1 must never *originate* a message. v2's scheduler + DB is the sole source
> of anything that goes out. v1 survives only as the transport pipe v2 sends
> through (plus the `#ask-gsa` RAG responder and a minimal built-in command set).**

## 4. The Primitives v2 Exposes

| # | Primitive | Status today | Covers |
|---|-----------|--------------|--------|
| 1 | **Scheduled & recurring posts** — publish content, to channels, on a cadence | Exists (`post_templates`, `v2/core/publishing/scheduler.py`) | weekly digest, MathCafe, any "post X every week" |
| 2 | **Event reminders** — data-driven from events table; reminders auto-fire | Exists (`event_reminders`, `materialize_event_reminders`) | event reminders, "this week" digests |
| 3 | **Connectors** — Discord, Telegram, pluggable future platforms | Exists (`v2/core/connectors/registry.py`) | multi-platform delivery |
| 4 | **Admin-defined commands** — orgs register their own slash commands from the dashboard | **Does not exist** | replacing hardcoded `/`-commands |
| 5 | **Extensions / integrations** — generic "poll external API → produce posts" | **Does not exist** | World Cup tracker class; any future external feed |

Primitives 1–3 already exist and are what v2's scheduler runs today. Primitives 4
and 5 are net-new platform capabilities.

## 5. Implementation Path (phased)

### Phase 0 — Enforce the boundary (the verifiable cut)

The first deliverable. After Phase 0, the **only** thing that can send
autonomously is v2.

**Remove every v1 originator:**
- Disable / remove the v1 scheduler cog (`bot/services/scheduler.py`): `daily_digest`,
  `check_upcoming_reminders`, `check_worldcup`, `worldcup_daily_schedule`,
  `post_mathcafe_daily`. Remove `"bot.services.scheduler"` from `EXTENSIONS`
  (`bot/main.py:43`).
- Remove all `/admin_*` commands (`bot/commands/admin.py`) — admin control moves
  to the dashboard. This includes the broadcast sends at `admin.py:190/199/689/768/830`.
- Remove `/ask` (`bot/commands/ask.py`) — the `#ask-gsa` RAG channel replaces it.
- Remove the MathCafe autonomous sender (`bot/services/mathcafe.py` scheduled post path).

**v1 keeps only:**
- The transport: the live Discord client (`bot/main.py`) and Telegram client
  (`run_telegram.py`) that v2 publishes *through*.
- The `#ask-gsa` RAG free-form chat responder (`bot/commands/chat.py`) — request/response,
  user-initiated, never autonomous. This is the "ask" surface.
- Minimal built-in commands: **`/help`** and **`/contact`**. (Other v1 commands —
  `/events`, `/initiative`, `/feedback`, `/resources`, `/qrcode`, `/worldcup` — are
  retired from the built-in set; their function moves to the dashboard/website or
  to admin-defined commands in later phases.)

**Verification (must pass before Phase 0 is "done"):**
1. Static: `grep` audit shows no `@tasks.loop` / autonomous `channel.send` remaining
   in v1 outside the transport + RAG path.
2. Runtime: restart the process; logs show `V2 Scheduler active` and **no** v1
   scheduler start lines. Confirm no `daily_digest` / digest post fires.
3. Behavioral: the next scheduled outbound (if any) is sourced from the v2 `posts`
   table only.

### Phase 1 — Reproduce killed features as v2 config

Using primitives 1–3 (already present): the weekly digest and event reminders
become **dashboard entries / DB rows**, not code. No new hardcoded senders.

### Phase 2 — Admin-defined commands (primitive 4)

Dashboard surface for an org to register its own slash commands (name, response,
behavior). Built-ins shrink to the true minimum. This is what lets each university
extend the bot without code.

### Phase 3 — Extensions framework (primitive 5)

A generic "external feed → posts" integration type, configurable from the
dashboard. World Cup returns as a *configured extension*; so can any future
external-data feature.

## 6. Out of Scope (this spec)

- Dashboard UI implementation details (separate spec per phase).
- Multi-tenant auth / org isolation specifics (assumed to exist in v2 schema;
  refined when Phase 2 lands).
- Migrating historical v1 data beyond what `v2/scripts/migrate_v1_to_v2.py` covers.

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Removing v1 senders silently drops a feature users rely on | Phase 1 reproduces the digest + reminders as v2 config before announcing parity |
| v2 rides on v1's client; killing too much breaks transport | Phase 0 explicitly preserves the client process + RAG + minimal commands |
| World Cup / MathCafe regressions | Accepted: they go dark in Phase 0, return as config in Phase 3. User approved this trade-off. |
| Backups before destructive removal | Mandatory un-skippable backup before Phase 0 edits (per established workflow) |

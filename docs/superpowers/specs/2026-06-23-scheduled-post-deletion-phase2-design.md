# Design — Scheduled Post-Deletion Phase 2 (Telegram unsend + 24h default + opt-in UX)

**Date:** 2026-06-23
**Status:** Pre-build (design → senior-eng review → owner approval → TDD). Builds on Phase 0 (`29e11b0`) + Phase 1 (`5390b08…05adc27`, live).
**Author:** Mohammad (intent) + Kavosh.
**Parent spec:** `docs/superpowers/specs/2026-06-23-scheduled-post-deletion-design.md`.

---

## 1. Goal
Finish the scheduled-deletion feature: make **Telegram** messages actually deletable (Phase 1 shipped Discord deletion + GroupMe best-effort), wire **WorldCup** posts to auto-delete after 24h (solves the Telegram flood — the original motivation), and give a clean **opt-in UX with a configurable default**.

## 2. The locked model (from brainstorming)
**Auto-delete is OPT-IN.** A post is either:
- **forever** — nothing set (`delete_at IS NULL`); the default for normal posts (e.g. event announcements, which are records kept forever — the immortal-records hard line), OR
- **delete after N hours** — `delete_at = send-time + N·3600s`, where **N defaults to 24** and the admin may set **1 ≤ N ≤ 48**.

**48h is a hard ceiling, never exceeded** — because Telegram only lets a bot delete its own message within ~48h of sending. Capping every auto-delete at ≤48h means Telegram deletion *always* lands inside the window: no stuck messages, one uniform rule across Discord/Telegram/GroupMe. (No use case needs >48h: WorldCup = 24h; reminders/qual-notices are themselves short-lived ≤24–48h ephemeral posts that auto-delete, with fresh ones sent later.)

**One shared setting:** `default.auto_delete_hours` (default `24`), read by both WorldCup and the dashboard form pre-fill. A separate WorldCup knob is YAGNI (add later if ever needed). Code may pass an explicit N to override.

## 3. Parts

### Part A — Telegram deletion (unblocks the headline case)
- **`TelegramBroadcaster.delete(chat_id, message_id) -> bool`** (`bot/services/telegram_broadcaster.py`): `await self._bot.delete_message(chat_id=chat_id, message_id=int(message_id))`; return True on success; on `TelegramError`/Exception log + return False. (python-telegram-bot `delete_message` exists; the bot already deletes "thinking…" messages elsewhere, so the capability is proven.)
- **`TelegramClientAdapter.delete_message(channel, message_id)`** (`v2/integration/telegram_client.py`): `ok = await self.broadcaster.delete(channel, message_id)`; raise `RuntimeError` if not ok. (`channel` is the real `chat_id` Phase 0 stored in `post_deliveries.channel`.)
- **`TelegramConnector.delete_message` override** (`v2/core/connectors/telegram_connector.py`): mirror `_send` — try the adapter, return `DeliveryResult(True/False, "telegram", channel, message_id, error)`. (Replaces the inherited default-unsupported.)
- **48h-expiry handling at delete time:** Telegram returns an error like "message can't be deleted" for >48h-old messages. The deleter currently treats unknown errors as transient (retry to cap). Add: a Telegram error matching the expiry/"can't be deleted" signal is **terminal** (`delete_failed`), not retried. Since we cap delete_at at ≤48h this is rare, but it's the safety rail.

### Part B — WorldCup 24h auto-delete
- **`MatchWatcher._post()`** (`v2/integration/match_watcher.py`): set `delete_at` on every enqueued WC post (kick-off / goal / correction / full-time / preview) to `now + default_hours`. It reads `default.auto_delete_hours` from settings (fallback 24). So all WC posts self-delete ~24h after sending → Telegram (and Discord) flood cleared automatically.
- `enqueue_post`/`PostDraft.delete_at` already exists (Phase 1); MatchWatcher just passes it.

### Part C — Configurable default + opt-in UX
- **Setting `default.auto_delete_hours`** (default `24`): add to the settings defaults; editable in the dashboard **Settings** tab (a small "Auto-delete default (hours)" field; validated 1–48).
- **Dashboard create-post form** (`dashboard/app.js`): replace the raw "Auto-delete at" datetime field (added in Phase 1) with an **opt-in control**:
  - a checkbox **"Auto-delete this post"** (unchecked = forever),
  - when checked, an **"after [N] hours"** number input, pre-filled from `default.auto_delete_hours`, min 1, max 48,
  - on submit, compute `delete_at = (scheduled_for or now) + N·3600s` (UTC) and send it; unchecked → omit `delete_at`.
- **Server-side clamp** (`v2/local_server.py _post_post`): if `delete_at` is set and is >48h after the send time, **clamp it to send+48h** (don't reject/lose the post; the UI already enforces max 48, so this is defensive belt-and-suspenders for the Telegram ceiling).

## 4. Data flow (delete path, end-to-end after Phase 2)
post enqueued with `delete_at` → sent (Phase 0/1 captures real ids incl. Telegram chat_id) → 30s scheduler tick runs `delete_due()` → for each delivery, `registry.delete_delivery(platform, channel, message_id)` → **Discord** unsends, **Telegram** unsends (Part A), **GroupMe** → `delete_unsupported` → per-delivery `delete_status` recorded, post `deleted_at` rolled up. Records immortal (UPDATE-only).

## 5. Error handling
- Telegram delete success → `deleted`; not-found/already-gone → `deleted` (idempotent); >48h expiry / "can't be deleted" → **terminal `delete_failed`** (no retry); transient (network/5xx/429) → retry to `MAX_ATTEMPTS`. (Reuses Phase 1's classification, plus the terminal-expiry rule.)
- Setting/clamp guards keep `delete_at ≤ send+48h` so the expiry path is rarely hit.

## 6. Testing (TDD)
- Broadcaster `delete()` returns True on success, False on TelegramError (fake bot).
- Adapter `delete_message` returns on success, raises on failure.
- `TelegramConnector.delete_message` → `DeliveryResult(success=True, channel=chat_id, message_id)`; failure → `success=False`.
- Deleter: a Telegram delivery is now `deleted` (not `delete_unsupported`); a simulated >48h "can't be deleted" error → `delete_failed` terminal (NOT retried to cap).
- `MatchWatcher._post()` sets `delete_at ≈ now+24h` on enqueued posts (reads the setting; fallback 24).
- Setting default present (`default.auto_delete_hours == 24`); dashboard `_post_post` clamps a >48h `delete_at` to ≤48h (or rejects).
- Dashboard form (JS) computes `delete_at` from N hours when the checkbox is on, omits when off (logic-level test in `posts_logic` if feasible, else manual-verify note).
- No regression: Phase 1 deletion suite, worldcup, connectors, publisher.
- Grow `eval`/verification notes as applicable.

## 7. Goals checklist (shipped / deferred)
- [ ] Telegram `delete()` + adapter + connector override → Telegram messages unsendable.
- [ ] Telegram >48h expiry = terminal `delete_failed` (safety rail).
- [ ] WorldCup posts auto-delete at `default.auto_delete_hours` (24h) via `MatchWatcher._post`.
- [ ] `default.auto_delete_hours` setting (default 24) + dashboard Settings field (1–48).
- [ ] Dashboard opt-in "Auto-delete this post" + "after N hours" (default 24, min 1, max 48); `delete_at` computed; off = forever.
- [ ] Server-side clamp `delete_at ≤ send+48h`.
- [ ] No regression (Phase 1 deletion, worldcup, connectors, publisher).
- [ ] **Deferred (flag explicitly):** separate WorldCup auto-delete knob (using the shared setting for now); cancel/extend pending deletions in the dashboard; offline `changes.sql` `delete_at` (owner-accepted, server-mode only); reminder auto-generation (separate feature).

## 8. Out of scope
- Auto-generating reminder posts from events (separate feature; this only honors `delete_at`).
- Per-platform different windows / >48h deletion (Telegram can't, and the uniform 48h cap is the decision).

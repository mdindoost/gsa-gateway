# Design — Scheduled Post-Deletion Phase 2 (Telegram unsend + 24h default + opt-in UX)

**Date:** 2026-06-23
**Status:** Senior-eng reviewed — **APPROVE-WITH-CHANGES** (2026-06-23); 4 must-fixes folded in
(live-DB settings seeding via `get_setting` fallback + idempotent seed; both WC enqueue sites
`_post`+`_post_preview`; connector-side `telegram.error` classification incl. `Forbidden`→terminal +
narrow expiry matcher; clamp baseline = `scheduled_for or now`, UX adds N·3600 to the UTC value).
Ready for TDD build. Builds on Phase 0 (`29e11b0`) + Phase 1 (`5390b08…05adc27`, live).
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
- **Classify errors in the connector, not by string-sniffing in the deleter (review must-fix).** `TelegramConnector.delete_message` catches specific `telegram.error` classes and surfaces a normalized signal in `DeliveryResult.error` so the deleter keys on something stable:
  - `telegram.error.Forbidden` ("not enough rights" — bot lacks `can_delete_messages` admin in the channel) → **terminal** (`delete_failed`); do NOT retry an unfixable permission error 5×.
  - `telegram.error.BadRequest` with a **narrow, anchored** message — only `"message can't be deleted"` / `"message can not be deleted"` (the >48h expiry) → **terminal** (`delete_failed`). Do NOT use a loose `"deleted"`/`"can't be deleted"` substring (false-terminal risk).
  - `"message to delete not found"` / already-gone → **success** (`deleted`, idempotent).
  - `telegram.error.RetryAfter` (429) / network/5xx → **transient** (retry to `MAX_ATTEMPTS`).
  - **Permission caveat:** a bot deleting its OWN message in a broadcast **channel** generally needs "Delete Messages" admin rights; without them Telegram raises `Forbidden`. (One-time channel-admin setup, outside code.)
  Since we cap `delete_at` ≤48h and the poller ticks every 30s, the expiry path should essentially never fire — the cap is the real guarantee; the terminal branches are the safety rail.

### Part B — WorldCup 24h auto-delete
- Set `delete_at = now + default_hours` on every enqueued WC post. **Two enqueue sites must both be touched** (review catch): `MatchWatcher._post()` (kick-off / goal / correction / full-time) AND `MatchWatcher._post_preview()` (the T-5 preview — the most ephemeral; it's a separate enqueue call). The deliberate decision: **all WC event types auto-delete**, including `fulltime` (results are easily re-found; the channel is ephemeral by design) — uniform, not an unconsidered sweep.
- `MatchWatcher` has `self._conn` + `self.org_id` (set in `start()`), so read `get_setting_typed(self._conn, self.org_id, "default.auto_delete_hours", 24)` per post (no caching — low frequency, and an admin's change takes effect immediately).
- `enqueue_post`/`PostDraft.delete_at` already exists (Phase 1); MatchWatcher just passes it. The WC path does NOT clamp (it's always 24h ≤ cap; the Settings 1–48 validation guards the setting value).
- **Backfill: accepted, not built.** WC posts enqueued BEFORE Phase 2 have `delete_at IS NULL` → they live forever and won't be cleaned. Acceptable (WC posts are short-lived; newly-sent ones self-clean going forward). Documented, not backfilled.

### Part C — Configurable default + opt-in UX
- **Setting `default.auto_delete_hours`** (default `24`):
  - **Seeding (review must-fix):** adding it to `ROOT_SETTINGS` is NOT enough — `seed_settings` runs only at v1→v2 migration, and the live DB is already migrated, so it would never appear. So: (a) every read uses `get_setting_typed(conn, org_id, "default.auto_delete_hours", 24)` — the code-level `24` is the real fallback; AND (b) add an idempotent seed on the **root (njit) org** (a small gated migration or a startup `INSERT OR IGNORE INTO settings`), matching where `default.send_time` lives, so the Settings field shows a value.
  - **Editable in the dashboard Settings tab** via the existing `/settings` write path; **validate 1–48 server-side** (not just client-side — `_post_post`'s clamp only protects per-post `delete_at`, not the setting value itself).
- **Dashboard create-post form** (`dashboard/app.js`): replace the raw "Auto-delete at" datetime field (added in Phase 1) with an **opt-in control**:
  - a checkbox **"Auto-delete this post"** (unchecked = forever),
  - when checked, an **"after [N] hours"** number input, pre-filled from `default.auto_delete_hours`, min 1, max 48,
  - on submit, **convert the local send time to UTC first** (`PL.localToUTC`), THEN add `N·3600s` (UTC arithmetic is DST-safe) → `delete_at`; unchecked → omit `delete_at`.
- **Server-side clamp** (`v2/local_server.py _post_post`): the post isn't sent yet (`sent_at` is NULL at create), so the clamp baseline is **`(scheduled_for or now)`**, not `sent_at`: if `delete_at > (scheduled_for or now) + 48h`, **clamp it to that + 48h**. Small send-drift vs `scheduled_for` is harmless (still inside Telegram's window). Defensive belt-and-suspenders; the UI already enforces max 48.

## 4. Data flow (delete path, end-to-end after Phase 2)
post enqueued with `delete_at` → sent (Phase 0/1 captures real ids incl. Telegram chat_id) → 30s scheduler tick runs `delete_due()` → for each delivery, `registry.delete_delivery(platform, channel, message_id)` → **Discord** unsends, **Telegram** unsends (Part A), **GroupMe** → `delete_unsupported` → per-delivery `delete_status` recorded, post `deleted_at` rolled up. Records immortal (UPDATE-only).

## 5. Error handling
- Telegram delete success → `deleted`; not-found/already-gone → `deleted` (idempotent); >48h expiry / "can't be deleted" → **terminal `delete_failed`** (no retry); transient (network/5xx/429) → retry to `MAX_ATTEMPTS`. (Reuses Phase 1's classification, plus the terminal-expiry rule.)
- Setting/clamp guards keep `delete_at ≤ send+48h` so the expiry path is rarely hit.

## 6. Testing (TDD)
- Broadcaster `delete()` returns True on success, False on TelegramError (fake bot).
- Adapter `delete_message` returns on success, raises on failure.
- `TelegramConnector.delete_message` → `DeliveryResult(success=True, channel=chat_id, message_id)`; failure → `success=False`.
- Deleter: a Telegram delivery is now `deleted` (not `delete_unsupported`); a `"message can't be deleted"` (expiry) → `delete_failed` terminal (NOT retried to cap); a `Forbidden`/"not enough rights" → `delete_failed` terminal (NOT retried 5×); a `RetryAfter`/network error → transient (retries).
- **`MatchWatcher._post()` AND `_post_preview()` both set `delete_at ≈ now+24h`** on enqueued posts (read the setting; fallback 24).
- **Setting absent → reads default 24** (proves the live-DB `get_setting_typed(..., 24)` fallback, since the key isn't seeded by `create_all`).
- Dashboard `_post_post` clamps a `delete_at` > `(scheduled_for or now)+48h` down to that bound; setting-write validates 1–48 server-side.
- Dashboard form (JS) computes `delete_at` from N hours when the checkbox is on, omits when off (logic-level test in `posts_logic` if feasible, else manual-verify note).
- No regression: Phase 1 deletion suite, worldcup, connectors, publisher.
- Grow `eval`/verification notes as applicable.

## 7. Goals checklist (shipped / deferred)
- [ ] Telegram `delete()` + adapter + connector override → Telegram messages unsendable.
- [ ] Connector classifies `telegram.error` → terminal (`Forbidden`, narrow expiry `BadRequest`) vs transient (`RetryAfter`/net) vs success (not-found); deleter keys on the normalized signal (no loose string-sniff).
- [ ] WorldCup posts auto-delete at `default.auto_delete_hours` (24h) via **both `_post` AND `_post_preview`**.
- [ ] `default.auto_delete_hours` setting (default 24): `get_setting_typed(...,24)` fallback everywhere + idempotent root-org seed (NOT ROOT_SETTINGS-only); dashboard Settings field, validated 1–48 server-side.
- [ ] Dashboard opt-in "Auto-delete this post" + "after N hours" (default 24, min 1, max 48); `delete_at` = UTC-send-time + N·3600; off = forever.
- [ ] Server-side clamp `delete_at ≤ (scheduled_for or now)+48h`.
- [ ] No regression (Phase 1 deletion, worldcup, connectors, publisher).
- [ ] **Deferred (flag explicitly):** separate WorldCup auto-delete knob (using the shared setting for now); cancel/extend pending deletions in the dashboard; offline `changes.sql` `delete_at` (owner-accepted, server-mode only); reminder auto-generation (separate feature).

## 8. Out of scope
- Auto-generating reminder posts from events (separate feature; this only honors `delete_at`).
- Per-platform different windows / >48h deletion (Telegram can't, and the uniform 48h cap is the decision).

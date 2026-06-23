# Design — Scheduled deletion of already-sent posts

**Date:** 2026-06-23
**Status:** Phase 0 SHIPPED (`29e11b0`, live). Phases 1–2 designed below, pre-build (need owner sign-off → TDD per the EXPERT-REVIEW gate).
**Author:** Mohammad (intent) + Kavosh, after a senior-eng design review of both approaches.

---

## 1. Problem & use cases
Every outgoing message goes through the posting system (`posts` → `PostPublisher` → `ConnectorRegistry` → connectors → `post_deliveries`). We want a post to optionally **self-delete** — unsend the delivered platform message(s) when a timer passes. Driving cases:
- **WorldCup posts → flooding.** Dozens of goal/kick-off/full-time messages bury the Telegram channel; once a match is old they're noise. Delete ~24h after sending.
- **Event reminders (1-week / 1-day before) → staleness.** The original announcement is a record (keep forever); the reminders are nags that mean nothing after the event. Delete after the event.
- **Qual-exam notices (1-week / 1-day before) → staleness + privacy.** A named student's exam notice shouldn't linger; both are temporary, delete after the exam.

Unifying rule: a post carries an optional **"good-until" time**; when it passes, unsend the delivered messages. Records simply have no such time (= keep forever). The admin OR code sets the time — dashboard field, or `enqueue_post(delete_at=...)`. How the time was computed (relative "now+24h" vs event-anchored) is the caller's concern; the feature just honors an absolute `delete_at`.

## 2. HARD LINE — post records are immortal
(In CLAUDE.md, commit `d365e2d`.) Deletion removes ONLY the message FROM the platform and **marks** the DB record. `posts` and `post_deliveries` rows are NEVER deleted/anonymized — they are the permanent audit of who/how/where/when. "Delete" = unsend, not forget. Applies even to privacy-sensitive posts (qual exam): the message leaves the channel, the record stays. The deleter therefore only ever `UPDATE`s; a test asserts it issues no `DELETE`.

## 3. Architecture (Approach 1 + per-delivery state)
A direct mirror of the proven send path:
- **`delete_at` on `posts`** = the per-post intent/schedule (one value per post; NULL = keep forever).
- **Per-delivery deletion outcome on `post_deliveries`** — because one post can be `deleted` on Discord but `delete_unsupported` on GroupMe at the same time; a single post-level flag can't represent that honestly (and would undercut the audit).
- **`PostDeleter.delete_due()`** mirrors `PostPublisher.publish_due()`: poll for due posts, read their `post_deliveries` rows, unsend each via the registry, mark outcomes.
- Runs in the existing `SchedulerRunner` loop, **after** `publish_due()` (disjoint rows — `status='sent'` vs `'scheduled'` — but explicit ordering is clearest).

(Approach 2 — a separate `post_deletions` queue — was rejected: duplicates state already on `posts`, adds a second source of truth, buys nothing at this scale.)

### Platform reality
- **Discord** — `channel.get_partial_message(int(message_id)).delete()`; channel resolved by name (`DiscordClientAdapter._resolve`). Real `msg.id` already stored. No time window. ✅
- **Telegram** — `bot.delete_message(chat_id, message_id)`. Real ids now stored (Phase 0). **48h window** for a bot deleting its own messages → must be guarded. ✅ (after Phase 0)
- **GroupMe** — bot API has **no delete endpoint** → best-effort `delete_unsupported` (message stays, record reflects it). ❌→ unsupported.

## 4. Data model (schema changes, idempotent `create_all`)
**`posts`** — add:
- `delete_at TEXT` — UTC "YYYY-MM-DD HH:MM:SS"; NULL = keep forever.
- `deleted_at TEXT` — convenience rollup: stamped when all deletable deliveries are resolved. Derived, not authoritative.

**`post_deliveries`** — add per-delivery deletion state:
- `delete_status TEXT CHECK (delete_status IN ('deleted','delete_unsupported','delete_failed','not_applicable'))` — NULL = not yet attempted.
- `deleted_at TEXT` — when this delivery's platform message was unsent.
- `delete_error TEXT` — last error (for `delete_failed`).
- `delete_attempts INTEGER NOT NULL DEFAULT 0` — bounds retries.

`not_applicable` = the delivery never produced a real, deletable message (e.g. a `failed` send, or a synthetic/sentinel message_id) → nothing to unsend, mark done.

Migrations: `ALTER TABLE ... ADD COLUMN` guarded in `create_all()` (CLAUDE.md: schema migrations are idempotent; STRICT tables, nullable adds are safe).

## 5. Connector interface
Add to `BaseConnector` a **non-abstract default** so send-only platforms inherit best-effort:
```python
async def delete_message(self, channel: str | None, message_id: str) -> DeliveryResult:
    return DeliveryResult(success=False, platform=self.name, channel=channel,
                          message_id=message_id, error="delete unsupported")
```
- **DiscordConnector** overrides → adapter `delete_message(channel, message_id)`: `_resolve(channel)` then `ch.get_partial_message(int(message_id)).delete()`.
- **TelegramConnector** overrides (Phase 2) → adapter → `broadcaster.delete(chat_id=channel, message_id=int(message_id))`.
- **GroupMeConnector** inherits the default → `delete_unsupported`.

Route deletions **through the registry** (`registry.delete_delivery(platform, channel, message_id)`) so failure isolation + the single-writer-to-`post_deliveries` discipline are preserved. Note: `DeliveryResult.status` maps only to success/failed for *sends* — do NOT overload it; the deleter maps the result to the richer `delete_status` set itself (a not-found/404 maps to `deleted`).

**`post_deliveries.channel` is per-platform, by design (Phase-0 retro finding).** Its meaning diverges:
Discord stores the channel **name** (`_resolve`d to an object at send AND at delete time), Telegram now
stores the resolved numeric **chat_id** (what `deleteMessage` needs), GroupMe the setting value. This is
intentional, not a bug: **each connector writes into `channel` whatever IT needs to later unsend, and
interprets it in its OWN `delete_message`.** The deleter never interprets `channel` itself — it just hands
`(channel, message_id)` to the right connector. **Phase 1 must (a) document this contract in the
`schema.py` `channel` column comment, and (b) keep deletion strictly per-connector** so the divergence
stays encapsulated.

## 6. `PostDeleter.delete_due()` (the executor)
```
now = utcnow
due = SELECT id FROM posts
      WHERE delete_at IS NOT NULL AND delete_at <= now
        AND status='sent' AND deleted_at IS NULL
for post in due:
  deliveries = SELECT * FROM post_deliveries WHERE post_id=? AND delete_status IS NULL
  for d in deliveries:
     if d.status != 'success' or not _is_real_id(d):   # nothing was delivered / synthetic id
         mark d delete_status='not_applicable'; continue
     result = registry.delete_delivery(d.platform, d.channel, d.message_id)   # via connector
     classify:
        success OR not-found/404            -> 'deleted'      (idempotent: goal = message absent)
        unsupported (GroupMe / default)     -> 'delete_unsupported'
        transient error, attempts < MAX     -> leave NULL (retry next tick), delete_attempts += 1, record delete_error
        terminal error / window expired / attempts == MAX -> 'delete_failed'
     UPDATE that post_deliveries row (delete_status, deleted_at, delete_error, delete_attempts)
     commit per delivery (so a crash mid-fan-out never re-deletes a done one)
  if every delivery row now has a non-NULL delete_status (no NULL left to retry):
     UPDATE posts SET deleted_at=now WHERE id=?
```
**Key rules (reviewer must-haves):**
- **404 / "message not found" = success** (`deleted`) — the goal state (message gone) is achieved; makes the executor idempotent across a crash between unsend and stamping.
- **Stamp per delivery as each completes** (not one post-level stamp) — crash-safety.
- **Telegram 48h window:** treat "message can't be deleted"/expiry as **terminal** (`delete_failed`), never infinite retry. Also guard at *set* time (see §7).
- **Skip synthetic ids:** GroupMe (`groupme:…`) and any pre-Phase-0 sentinel never reach a real delete — `not_applicable` or `delete_unsupported`.
- **Bounded retries:** `delete_attempts` cap (e.g. 5) → `delete_failed`.
- Wrap the whole sweep so an exception never kills the scheduler loop (mirror the existing `tick()` guard).

## 7. Setting `delete_at`
- **Code:** `PostDraft.delete_at` + `enqueue_post` persists it. WorldCup's `MatchWatcher._post()` passes `delete_at = sent/now + 24h` (hardcoded or config — owner's choice).
- **Dashboard:** a "Delete after / keep until" control on the create-post form (`/posts` endpoint in `local_server.py` + `app.js`): either a duration (24h / 7d / …) or an absolute datetime, converted to UTC `delete_at`. Empty = keep forever (default — unchanged behavior).
- **Telegram 48h guard at set-time:** if the post targets Telegram and `delete_at` is > ~47h after the expected send, warn (dashboard) / clamp or document (code), so we don't schedule a delete the platform will refuse.
- **Visibility:** dashboard shows a post's `delete_at` and, after deletion, the per-platform `delete_status` (sent → auto-deleted/unsupported). (A cancel/extend control on pending deletions is a nice-to-have, not Phase 1.)

## 8. Phasing
- **Phase 0 — SHIPPED (`29e11b0`):** capture real Telegram `message_id` + `chat_id` on every send. (Was the blocking prerequisite.)
- **Phase 1:** schema (§4) + `delete_message` default + Discord override + registry routing + `PostDeleter.delete_due()` + scheduler wiring + `PostDraft.delete_at`/`enqueue_post` + dashboard field. → **Discord deletion works immediately.** GroupMe best-effort.
- **Phase 2:** Telegram `delete_message` (broadcaster `delete()` + adapter + connector override) + the 48h guard. → **Telegram deletion works → solves the WorldCup flood** (the headline case).

## 9. Testing (TDD)
- Schema: new columns exist; `create_all()` idempotent.
- Connector: default `delete_message` returns `delete_unsupported`; Discord override calls delete with the right id; Telegram override (Phase 2) calls `bot.delete_message(chat_id, message_id)`.
- `PostDeleter.delete_due()`: selects only due+sent+not-already-deleted; marks `deleted` on success; **404 → `deleted`**; GroupMe → `delete_unsupported`; transient → retry then `delete_failed` at cap; stamps post `deleted_at` only when all deliveries resolved; **idempotent** (re-run after a simulated crash doesn't double-delete / errors cleanly).
- **Immortal-records test:** the deleter issues no `DELETE` against `posts`/`post_deliveries` (assert only `UPDATE`).
- Telegram 48h: a `delete_at` beyond the window is guarded/terminal, not retried forever.
- enqueue_post persists `delete_at`; dashboard endpoint accepts and stores it.
- Add verification questions/cases to the suite (grow-the-suite rule).

**Phase 0 retro follow-ups to fold into Phase 1 (from the `29e11b0` review):**
- Registry-level test asserting the real `message_id`+`chat_id` actually land in `post_deliveries` (Phase 0's whole point is persistence; only the connector return is tested today).
- Connector test that the media/interactive path also yields the real id (only `send_text` is exercised today).
- Document the per-platform `channel` semantics in the `schema.py` `post_deliveries.channel` comment (see §5).
- (Minor, optional, repo-wide) migrate async tests off the deprecated `get_event_loop()` pattern.

## 10. Goals checklist (fill at PR — shipped / deferred, nothing silently dropped)
- [x] **Phase 0:** real Telegram message_id + chat_id captured on every send (`29e11b0`).
- [ ] `delete_at` + `deleted_at` on `posts`; per-delivery `delete_status`/`deleted_at`/`delete_error`/`delete_attempts` on `post_deliveries`.
- [ ] `delete_message` on `BaseConnector` (default unsupported) + Discord override + registry routing.
- [ ] `PostDeleter.delete_due()` wired after `publish_due()` in `SchedulerRunner`; 404=success; per-delivery stamping; bounded retries.
- [ ] `PostDraft.delete_at` + `enqueue_post`; dashboard "delete after/keep until" field.
- [ ] Immortal-records guard (deleter UPDATE-only + test).
- [ ] Telegram override + 48h window guard (Phase 2).
- [ ] GroupMe best-effort `delete_unsupported` throughout.
- [ ] **Deferred (flag explicitly):** cancel/extend pending deletions in the dashboard; auto-generation of the 1-week/1-day reminder posts (separate feature); chunked-message multi-id deletion (no chunking exists today).

## 11. Out of scope
- The reminder *generator* (auto-creating 1-week/1-day posts from an event) — separate feature; this design only honors a `delete_at` on whatever posts exist.
- Message chunking (Telegram sends one message today; if chunking is added later, `post_deliveries` would need multiple rows / a message-id list).

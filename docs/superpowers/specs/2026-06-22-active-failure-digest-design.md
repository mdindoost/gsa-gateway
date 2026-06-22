# Active Failure Digest — push "what's failing" instead of dashboard-pull (2026-06-22)

**Status:** DESIGN reviewed 2026-06-22 (senior-eng = BUILD-WITH-FIXES; all 5 fixes folded — see §9). Awaiting
owner approval → TDD build. Accuracy-backlog item #3 ("the flag that tells us something's wrong"). Observability
only — senior-eng review; NOT a retrieval/answer change → no RAG review.
**Related:** `docs/superpowers/findings/2026-06-22-accuracy-observability-and-feedback-backlog.md` (the signals);
the buffered-lane generator pattern (`v2/integration/daily_quote.py`, `daily_fixtures.py`).

## 1. Problem
Failure signals exist (👎 with reason tags, confidence<50 "to add to KB", deflections) but are **dashboard-pull**
— nobody is told when answers fail. The 46 👎 sat unreviewed until 2026-06-22. We want an **active push**: a
periodic digest of failures delivered to the owner, reusing trusted infra, with zero answer-path risk.

## 2. Goal / non-goals
**Goal:** a scheduled digest of recent failures (👎 + low-confidence + counts) pushed to a configurable admin
destination, owner-set cadence, idempotent. **Non-goals:** any change to answers/retrieval; real-time alerting
(daily batch is enough); dashboard changes; PII handling beyond what's already stored (user IDs already hashed;
the digest shows question TEXT — hence an ADMIN-only destination).

## 3. Design — a buffered-lane `PostSource` (reuse, no new delivery infra)
A new `FailureDigestSource(PostSource)` in `v2/integration/failure_digest.py`, modeled on `DailyQuoteSource`:
- **`poll()` → `list[PostDraft]`** ([R3] — returns `[draft]` or `[]` when quiet, NOT `PostDraft|None`; matches the
  real `PostSource` contract). `PostDraft.type` must be an ALLOWED value (`"digest"`); `source_type` (free-form,
  e.g. `"failure-digest"`) scopes dedup. **Idempotent: `dedup_key = "failure-digest-<YYYY-MM-DD>"`** → enqueue_post
  dedups on `(org_id, source_type, _dedup_key)` → one digest/day across restarts (CONFIRMED by review).
- The draft body (pure formatter, unit-testable) reports, over the period window (last 24h for daily):
  - **👎 list** (top N): `question_text` + reason tag (`off_topic`/`incomplete`/`wrong_info`) + confidence.
  - **Low-confidence questions** (top N, `confidence < 50`, grouped, with count) — the "add to KB" list.
  - **Counts**: total questions, 👎/👍/🔄, deflections, and the answer-rate **explicitly labelled a vanity
    metric** (the real signal is 👎) per the findings doc.
- **Read-only** queries on `questions` + `response_feedback`. No writes except the enqueued `posts` row.
- **[R1 — the window bug] The time window MUST use an ISO-`T` boundary**, NOT `datetime('now',-N days')`. Stored
  timestamps are `2026-06-22T13:13:48.228843+00:00` (ISO, `T` separator); `datetime('now',...)` yields a
  space-separated string that sorts BEFORE `T`, over-counting the window by ~48% (222 vs 150 rows measured).
  Build the boundary in Python: `(datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS)).isoformat()` and
  compare `WHERE timestamp >= ?` (matches exactly how `log_question` writes). Test a row at `boundary − 1s` is excluded.
- **[R5 — NULL confidence] Decision: NULL `confidence` is NOT low-confidence** (treated as "unscored", excluded
  from the `confidence < 50` list — SQLite `< 50` already excludes NULL). Stated + tested explicitly.

## 4. Delivery (env-configurable — owner picks the destination) [owner: "decide during design"]
Reuse the `platform_channels` / channel-name mechanism the fixtures digest uses. New env:
- `FAILURE_DIGEST_ENABLED` (default `0` — off until configured),
- `FAILURE_DIGEST_CHANNEL` (admin channel name — e.g. `gsa-ops`; MUST be admin-only, never a student channel),
- `FAILURE_DIGEST_PLATFORMS` (e.g. `discord` and/or `telegram`),
- `FAILURE_DIGEST_HOUR_ET` (default e.g. 9), `FAILURE_DIGEST_PERIOD_DAYS` (default 1).
Wired in `main.py` exactly like the WC fixtures digest (gated; needs `V2_SCHEDULER_ENABLED` so drafts deliver;
resolves the org by slug). Delivery flows draft → `enqueue_post` → `posts` row → v2 SchedulerRunner → ConnectorRegistry.
- **[R2 — hour scheduling] The `SourceRunner` does NOT schedule by hour** — it polls every `interval` (use 3600s,
  harmless, dedup makes re-polls no-ops, no external API). The "deliver at HOUR_ET" behaviour is set on the draft
  via `scheduled_for = morning_utc(day, hour_et)` (reuse `daily_fixtures.morning_utc`); the v2 SchedulerRunner holds
  the row until then.
- **[R4 — no-channel guard, privacy in code] If `FAILURE_DIGEST_CHANNEL` is unset, do NOT start the runner**
  (log a warning + skip — never fall back to `platform_channels()` defaults or any student channel). `enqueue_post`
  does not restrict the target channel, so admin-only visibility is a deployment guarantee (the named channel must be
  admin-only) BACKED by this code guard. Tested: no channel configured ⇒ runner not started.

## 5. Empty/quiet handling
When the window has **no 👎 and nothing notable**, `poll()` returns `None` (no post) to avoid daily noise — a
"no news is good news" default. (Optional `FAILURE_DIGEST_ALWAYS` to force an "all clear" heartbeat; default off.)

## 6. Safety / risk
- **No answer-path change** — purely reads analytics + enqueues an admin post. Cannot affect any user answer.
- **Privacy:** the digest shows question text → MUST go to an admin-only destination (enforced by config; documented).
- **Idempotency:** date dedup key prevents duplicate digests on repeated polls / restarts.
- **Failure isolation:** `poll()` wraps its DB read in try/except → returns `None` on error (never breaks the
  scheduler tick), mirroring the other generators.

## 7. Test plan (TDD)
- **Formatter (pure):** given fixture 👎 rows + low-conf rows → body contains the questions, reason tags, counts,
  and the "vanity metric" label; truncates to top N.
- **Empty window** → `poll()` returns `None` (no post).
- **Idempotency** → two polls same day → one draft (dedup key stable); next day → new key.
- **Read-only** → poll does not write to `questions`/`response_feedback`.
- **Window (R1)** → only rows within `PERIOD_DAYS` are included; a row at `boundary − 1s` is EXCLUDED (the ISO-`T`
  boundary bug regression test).
- **NULL confidence (R5)** → a NULL-confidence row is NOT counted as low-confidence.
- **No-channel guard (R4)** → with `FAILURE_DIGEST_CHANNEL` unset, the runner is not started.
- **poll() returns `[]`** (not None) on a quiet window; `[draft]` otherwise; the draft's `scheduled_for` = the
  HOUR_ET morning_utc.
- Use an in-memory/seeded DB fixture (like the judging/db tests), not the live DB.

## 8. Goals checklist (verify at build)
- [ ] `FailureDigestSource.poll()` → PostDraft|None, date dedup key, period window, read-only
- [ ] Pure formatter (top-N 👎 + reason tags + low-conf + counts + vanity-metric label) — unit tested
- [ ] Env-configurable delivery (ENABLED/CHANNEL/PLATFORMS/HOUR/PERIOD), default OFF, admin-only channel documented
- [ ] Wired in main.py like the fixtures digest (gated; needs v2 scheduler)
- [ ] Empty window → no post; optional ALWAYS heartbeat flagged
- [ ] Failure-isolated poll() (never breaks the scheduler tick)
- [ ] (pairs with backlog #1/#3) — can later include the context-rewrite `original→resolved` audit pairs once persisted
- [ ] [R1] ISO-`T` time-window boundary (Python isoformat); boundary-exclusion test
- [ ] [R2] HOUR_ET via `scheduled_for=morning_utc(...)` on the draft (runner polls hourly + dedup)
- [ ] [R3] `poll() -> list[PostDraft]` (`[]` when quiet); `PostDraft.type="digest"`
- [ ] [R4] no-channel guard (unset CHANNEL ⇒ runner not started; no student-channel fallback)
- [ ] [R5] NULL confidence excluded from low-conf; tested

## 9. Design-review record (2026-06-22)
One senior-eng review — **BUILD-WITH-FIXES** (architecture sound, correctly reuses the buffered-lane infra;
PostDraft/PostSource/dedup/idempotency/failure-isolation/empty-window all VERIFIED). 5 fixes folded above:
- **R1 (real bug)** time window must use an ISO-`T` boundary, not `datetime('now',…)` — the space-vs-`T` sort
  mismatch over-counts ~48% (222 vs 150 rows). Build the boundary with Python `isoformat()`.
- **R2** HOUR_ET is enforced via `scheduled_for=morning_utc(...)` on the draft, not by the SourceRunner (which
  has no hour scheduling — it polls every interval + relies on per-day dedup).
- **R3** `poll()` returns `list[PostDraft]` (`[]` quiet), not `PostDraft|None`; `type` must be an allowed value.
- **R4** if `FAILURE_DIGEST_CHANNEL` unset → don't start the runner (privacy invariant enforced in code).
- **R5** NULL confidence is not low-confidence (excluded + tested).

*Next: owner approval → TDD build in a worktree off main → diff → sign-off → merge + restart + set the env destination.*

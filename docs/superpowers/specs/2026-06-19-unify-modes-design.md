# Unify Bot Modes — One Per-User Mode State (Task #10)

**Status:** DESIGN — REVISED after senior review · **Date:** 2026-06-19 · **Branch:** `feat/unify-modes`

> **Revision note (post-review):** The first draft *mirrored* a judging top-level enum into
> a shared store (two facts kept in sync by hand) — the reviewer correctly flagged this as
> "scattering with extra steps" (B1). This revision adopts **derive, don't mirror**: the
> judging session state machine remains the sole owner of "what judging sub-state," and the
> unified `ModeRegistry.get()` *computes* the effective mode by asking the judging manager
> first (`mode_of(uid)`), falling back to the conversation gsa/free bit. There is exactly
> one writer per fact. Also: the dispatch predicate is now stated precisely and the
> free→judge / substring-trigger cases are pinned down (B2). See "Senior review outcomes".

## Problem

The bot has **5 user modes** managed in **two disconnected subsystems** with no single
source of truth:

- **gsa** (default) + **free** ("general chat") — `bot/services/conversation.py`
  (`ConversationSession.mode`), toggled in `bot/core/message_handler.py` via
  `bot/services/intent_detector.py` phrase detection.
- **judge / presenter / audience** — `v2/core/judging/session.py`
  (`JudgingSessionManager`), an in-memory state machine, wired ONLY into the Telegram
  connector, which **intercepts messages before** the conversation handler ever runs.

They coexist by *implicit ordering* (`telegram_connector._on_message` calls
`judging_manager.handle()` first; if `consumed` is False it falls through to RAG). Nobody
owns "what mode is this user in"; the gsa/free bit and the judge/presenter/audience state
are independent and can silently disagree. The abad14e bug (free mode behaved like gsa
because `_try_structured` ran before the free-mode check) is a symptom of this scattering.

## Goal

ONE per-user mode value covering all 5 modes, with explicit transitions, per-mode handler
ownership, and ONE place to add a new mode. Judging and conversation both read/write the
**same** store. Replace the "judging intercepts first" implicit ordering with an explicit
mode dispatch.

## Constraints honored

- Preserve abad14e: free mode skips structured routing.
- Preserve every judging flow (PIN, attendance, audience-votes-once, judge auto-return).
- Preserve gsa/free toggle phrasing.
- Discord and Telegram run as **separate processes** (see `assistant.py` docstring); a
  store is per-process, in-memory. Discord never sees judging modes — that's fine; the
  *mechanism* is uniform, the *handler set* differs per platform.
- Per-message overhead must stay ~O(1) (one dict lookup); no new DB hits on the hot path.

## The model

### 1. `Mode` enum — the single vocabulary (`bot/core/modes/registry.py`)

```python
class Mode(str, Enum):
    GSA = "gsa"            # default — GSA knowledge (structured + RAG)
    FREE = "free"          # general chat — skip GSA knowledge, general LLM
    JUDGE = "judge"        # judging state machine (Telegram only)
    PRESENTER = "presenter"
    AUDIENCE = "audience"
```

`str`-valued so existing persisted/logged values (`mode="free"` in `log_question`,
`ConversationSession.mode == "gsa"`) keep working unchanged — `Mode.FREE == "free"` is
True. **Back-compat:** anywhere a bare string is read/written, the enum's value matches.

### 2. `ConversationModeStore` — owns the gsa/free bit ONLY (`bot/core/modes/registry.py`)

In-memory, per-process, keyed by `user_id` (raw, exactly like both current subsystems —
each platform is its own process so ids never collide; see assistant.py). Default = GSA.
It holds **only the conversation mode (GSA|FREE)** — the one fact with no other home. The
judging modes are NOT stored here; they are *derived* (next section), so there is a single
writer per fact and no mirror to keep in sync.

```python
class ConversationModeStore:
    def get(self, user_id) -> Mode            # default Mode.GSA; only ever GSA|FREE
    def set(self, user_id, mode: Mode) -> None
    def reset(self, user_id) -> None          # back to GSA (== remove entry)
```

Thread-safety: guarded by a `threading.Lock`. Rationale: discord.py and PTB are each
single-threaded async event loops, BUT `message_handler._try_structured` runs inside an
`asyncio.to_thread` worker thread and reads the mode while the event-loop thread may write
it (free/gsa toggle). The lock wraps only the dict op (nanoseconds); it never calls back
into async, so no deadlock. (Per S1: this lock protects the *store*; the judging
`_JudgeSession` mutation is serialized by PTB's effectively-serial per-update dispatch and
is out of this lock's scope — we do not over-claim system-wide thread-safety.)

**`ConversationManager` keeps NO mode of its own.** Its `get_mode`/`set_mode` become thin
delegators to the injected store (back-compat shim so existing call sites and the
`test_conversation.py` mode tests keep working), and `ConversationSession.mode` is retired
as authoritative state (kept as a harmless dead default; tests touch it only via
get/set — verified). This is the de-scattering: there is now exactly one place the gsa/free
bit lives.

### 3. `JudgingSessionManager` REMAINS the sole owner of judging state — derive, don't mirror

`JudgingSessionManager` owns the rich judging *sub-state* (PIN attempts, scoring progress,
pending vote) — genuinely a state machine that does **not** collapse into an enum, so it
stays untouched. We add ONE read-only projection and ONE trigger predicate; we do **not**
write a duplicate enum anywhere (no mirror → no sync hazard, the B1 fix):

```python
# new on JudgingSessionManager:
def mode_of(self, user_id) -> Mode | None:
    """Effective judging mode derived from the live session state, or None if not
    in any judging mode. idle/absent -> None; awaiting_pin|ready|confirming_presenter|
    scoring|confirming -> JUDGE; presenter_awaiting_number -> PRESENTER;
    audience_ready|audience_confirming -> AUDIENCE."""

def is_trigger(self, text) -> bool:
    """True iff text matches a judge/presenter/audience entry trigger (wraps the
    existing _RE_*_TRIGGER regexes). Deliberately keeps the existing loose re.search
    semantics so today's behavior (incidental 'judge mode' phrase routes to judging) is
    preserved exactly — no regression vs. the current 'judging intercepts first'."""
```

Because the judging mode is derived from `_sess(uid).state`, **every** existing
`_sessions.pop` / state transition (logout L94/L100, presenter-register-success L253,
audience-close L578, audience-vote-non-judge L617, the `_restore_from_audience` judge
branch L596/L609, and all the error early-returns) is automatically reflected with **zero
new code on those paths** — the projection reads the post-transition state. This is the key
property the mirror lacked: no path can leave the projection stale.

The manager is constructed with NO new required param; the shared store is wired only into
the dispatcher and `ConversationManager`. The existing 86 judging tests build the manager
with `db_path` only and pass unchanged (we only *added* two methods).

### 4. `ModeRegistry` — the single unified read of the effective mode
(`bot/core/modes/registry.py`)

One function answers "what mode is this user in," composing the two single-writer sources
(judging-derived first, then conversation):

```python
class ModeRegistry:
    def __init__(self, conv_store: ConversationModeStore, judging=None): ...
    def get(self, user_id) -> Mode:
        if self.judging is not None:
            jm = self.judging.mode_of(user_id)   # JUDGE|PRESENTER|AUDIENCE|None (derived)
            if jm is not None:
                return jm
        return self.conv_store.get(user_id)       # GSA|FREE
```

This is the "ONE place" to ask for a mode. Stats/debug compose the two single-writer views
here too — never a third copy.

### 5. `ModeDispatcher` — explicit ownership, no implicit ordering
(`bot/core/modes/dispatcher.py`)

The single entry point a connector calls. The ownership rule is now stated **precisely**
(B2): *judging owns the message iff the user is already in a judging mode, OR the user is
in a conversation mode (GSA|FREE) and the text is a judging entry trigger.*

```python
class ModeDispatcher:
    def __init__(self, registry, *, judging=None, conversation_handler): ...
    async def dispatch(self, user_id, text, *, make_request) -> Reply:
        mode = self.registry.get(user_id)              # unified, derived
        judging_owns = self.judging is not None and (
            mode.is_judging                            # already mid-judging
            or (mode in (Mode.GSA, Mode.FREE) and self.judging.is_trigger(text))
        )
        if judging_owns:
            resp, consumed = self.judging.handle(user_id, text)  # mutates its own state
            if consumed:
                return Reply.judging(resp)
            # not consumed only happens from idle+non-trigger, which judging_owns already
            # excludes; defensive fall-through to conversation.
        # CONVERSATION (gsa/free): message_handler owns the toggle + the abad14e
        # free-mode-skips-structured gate (both unchanged — they call get_mode/set_mode
        # which now hit ConversationModeStore).
        return await self.conversation_handler(make_request(user_id, text))
```

Key properties:
- **Explicit, not implicit.** The dispatcher *names* the ownership rule instead of relying
  on call order. The `mode = registry.get(...)` snapshot is used ONLY for the routing
  decision; `judging.handle` re-derives from `_sess` as the authority, so a stale snapshot
  can never corrupt state (S1).
- **One place to add a mode.** Two explicit, documented extension points (N1):
  a *conversation* sub-mode → the message handler's toggle block (Discord gets it free);
  a *dispatcher-owned* mode (judging-style) → a handler + its ownership predicate here.
- **`is_trigger`** lives on the judging manager (single owner of trigger knowledge); the
  dispatcher never duplicates the regexes.

### Senior review outcomes (how each finding was resolved)

- **B1 (mirror = re-scatter) — RESOLVED by derive-don't-mirror.** No duplicate enum is
  written; `mode_of()` projects the live session state. Single writer per fact.
- **B2 (dispatch predicate imprecise; free→judge) — RESOLVED.** Predicate stated exactly
  above; free→judge works (`is_trigger` short-circuits to judging); loose `re.search`
  trigger semantics preserved intentionally (documented, no regression). Covered by tests
  (a)–(f) + free→judge.
- **S1 (lock scope over-claim) — RESOLVED.** Lock documented as store-only; judging
  serialization noted; snapshot-not-authority noted.
- **S2 (ConversationSession.mode field tests) — VERIFIED.** `test_conversation.py` touches
  mode only via `get_mode/set_mode`; field retired as dead default. Confirmed by grep.
- **S3 / D2 (restart resets mid-judging to GSA) — ACCEPTED + added to eval as known
  behavior** so it isn't "fixed" by accident.
- **N1 (two extension points) — documented above.** **N2 (Mode(str,Enum) logging) —**
  `log_question` already receives the string value; one test asserts `"free"` persists.
  **N3 (grow eval) — done** (sequences a–f + free→judge added to eval/questions.txt-style
  assertions in the new unit test; user-facing mode phrasing added to eval/questions.txt).

### 6. Where each subsystem reads the mode

- `message_handler.handle` already calls `self.conversation_manager.get_mode(user_id)` —
  that now transparently reads the `ConversationModeStore` (delegation). **No change** to
  the abad14e gate or the free-mode RAG-skip — they keep calling `get_mode`/`set_mode`,
  which now hit the store. This is the crucial "don't break the fix" property: the fix's
  code path is untouched; only the backing storage moved.
- Telegram `_on_message` stops calling `judging_manager.handle()` directly; it calls
  `dispatcher.dispatch()`. Discord `chat.py` is unchanged (no judging); **D1: leave Discord
  calling `message_handler.handle` directly** (a dispatcher with `judging=None` is a pure
  pass-through — ceremony, not unification). The *store* is the unification; the dispatcher
  is only needed where >1 handler competes (Telegram).

## Dispatch flow (Telegram, all 5 modes)

```
incoming text ──> ModeDispatcher.dispatch(uid, text)
   │   mode = ModeRegistry.get(uid)   # judging.mode_of(uid) ?? conv_store.get(uid)
   │
   ├─ judging_owns = mode.is_judging OR (mode in {GSA,FREE} AND judging.is_trigger(text))
   │     └─ yes → judging.handle(uid, text)   # mutates _sess; mode_of() re-derives
   │               consumed? → reply, done.
   │
   └─ else → conversation_handler(MessageRequest)   (message_handler.handle)
             └─ get_mode(uid) from ConversationModeStore: GSA → structured+RAG |
                FREE → general LLM   (free/gsa toggle updates store via set_mode)
```

## Migration / back-compat path

1. Add `bot/core/modes/{__init__,registry,dispatcher}.py` (`Mode`,
   `ConversationModeStore`, `ModeRegistry`, `ModeDispatcher`) — pure new code, no behavior
   change yet.
2. `ConversationManager` gains an optional `mode_store` param; `get_mode/set_mode` delegate
   to it (private store when omitted); `ConversationSession.mode` retired as the source of
   truth. Existing `test_conversation.py` mode tests pass (delegation preserves semantics).
3. `JudgingSessionManager` gains `mode_of()` + `is_trigger()` (read-only additions, NO new
   constructor param, NO writes). Existing 86 judging tests pass unchanged.
4. `build_assistant` creates ONE `ConversationModeStore`, injects it into
   `ConversationManager`, and exposes it on the `Assistant` so the connector can build the
   `ModeRegistry`/`ModeDispatcher` with the SAME store.
5. `run_telegram.py` builds the `JudgingSessionManager`, then a `ModeRegistry(conv_store,
   judging)` and `ModeDispatcher(registry, judging=…, conversation_handler=handler.handle)`;
   `TelegramConnector` calls `dispatcher.dispatch()` instead of `judging_manager.handle()`.
6. No DB schema change. Mode is ephemeral per-process state (as it is today) — a bot
   restart resets everyone to GSA and clears in-progress judging, which is the **current**
   behavior (documented in `JudgingSessionManager`). We deliberately do NOT persist mode:
   the judging in-progress state is already non-persistent by design, and persisting just
   the top-level enum would create a new divergence (persisted enum vs. ephemeral
   sub-state). Flagged as a conscious decision.

## Implementation senior review — outcomes

A second senior review of the diff (correctness + efficiency) found **no blockers** in the
mode logic and confirmed: `_STATE_TO_MODE` covers all 8 non-idle states; `mode_of` uses
`_sessions.get` (no phantom session on read); the dispatcher routes every message identically
to the old "judging-first" ordering (the idle branch only ever consumed the same 3 triggers
`is_trigger` wraps); one `ModeRegistry.get` + at most 3 regexes per message, **zero added DB
hits** on the hot path; the store lock is correct/deadlock-free and `mode_of`/`_sessions` are
touched only from the PTB event-loop thread. Resolved findings:
- **#1 (red baseline) — FIXED.** Removed 8 stale `_cmd_events/_contact/_resources/_help`
  tests (v1 commands deleted in the all-conversational migration; failing on base too).
  `test_telegram_connector.py` is now fully green.
- **#2 (test read retired field) — FIXED.** `test_session_default_mode_is_gsa` →
  `test_default_mode_is_gsa`, asserting via `get_mode` (the real source of truth).
- **#5 gap — CLOSED.** Added `test_toggle_phrase_midjudging_owned_by_judging_not_store`:
  a judge typing "free mode" stays owned by judging and does NOT flip the conversation store.

## What this explicitly does NOT change

- Judging sub-state machine internals (PIN lockout, scoring, confirm, audience return).
- The RAG pipeline, structured router, live fallback, heads-up.
- Discord's lack of judging.
- Persisted/logged `mode` string values.

## Decisions (resolved)

- **D1 — Discord through the dispatcher?** NO. With `judging=None` the dispatcher is a
  pass-through; Discord has one handler. The *store* is the unification. Re-evaluate if
  Discord ever gains judging. (Two extension points documented in §5.)
- **D2 — Persist mode across restart?** NO (migration step 6). Restart resets everyone to
  GSA and clears in-progress judging — current behavior; persisting only the top-level enum
  would re-create a divergence. Added to eval as known/accepted behavior (S3).
- **D3 — Lock granularity.** One process-wide `threading.Lock` around the store's dict ops
  only. Scope is the store; not a system-wide thread-safety claim (S1).
```

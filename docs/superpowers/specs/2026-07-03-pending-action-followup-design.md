# Pending-Conversational-Action — follow-up / clarify resume (thread A)

**Date:** 2026-07-03
**Status:** DESIGN — awaiting senior-eng (+RAG) review and owner approval before build.
**Workstream:** the short-query / follow-up ledger — thread **A** (see
`memory/project_query_expander_removal.md`, `memory/project_followup_resume_bug.md`).
This is the **prerequisite** that unblocks thread **F** (confidence-gated clarify).

---

## 1. Problem (CONFIRMED, reproduced 2026-07-03)

The bot makes a good offer/clarify, the user accepts, and the bot ignores its own question —
routing the acceptance as a brand-new query and returning garbage.

```
USER: "who has the lowest citation in ywcc"
BOT:  "I can only rank people by highest citations … Want the most citations instead?"   ✅ good offer
USER: "yes"
BOT:  🌐 Live from NJIT's website: STEM OPT Extension reporting …                          ❌ garbage
```

Re-verified end-to-end against current code (`scratchpad/repro_followup.py`). Root cause = **two
compounding bugs**:

1. **The offer is never remembered.** A structured decline returns early at
   `bot/core/message_handler.py:289-292` (`return MessageResponse(text=structured)`), *before* the
   RAG pipeline's conversation-history write at `message_handler.py:920`. So `add_turn` is never
   called — conversation history is **empty** after the offer turn (verified). More broadly: **no**
   structured answer ever reaches conversation history, not just offers.
2. **No pending-action state.** Even with history, `"yes"` is not a referential follow-up (no
   pronoun), so `context_rewrite.is_follow_up("yes")` is `False` and rewrite is skipped
   (`bot/core/context_rewrite.py:27-33`); `resolve_query("yes") -> ("", False)`. And nothing stores
   the *offered action* (`top_people_by_metric{metric:citations, org:ywcc}`). So `"yes"` has nothing
   to resume → routed raw → garbage.

The only follow-up state today is `offer_live_search` — a bare bool on `MessageResponse`
(`message_handler.py:178`) the connector turns into a button. It is **not** a resumable action and
covers only the live-search button, not a typed `"yes"`.

### Why this gates the roadmap
Every offer/clarify the bot makes dead-ends identically. A **survey of the retrieval layer found
four** such points (§4). Fixing only the metric one would be a bandaid; the mechanism must be
general. Thread **F** (clarify) cannot ship on top of a dead-end follow-up path — it would just
multiply questions the bot can't hear the answer to.

---

## 2. Goals

- **G1** — A resumable **pending-conversational-action** state: when the bot offers/clarifies, it
  persists a structured, resumable action (an *option set*) keyed to the user, and records the offer
  turn in history.
- **G2** — Next turn, **deterministically** match the user's reply to the pending options and
  **execute** the chosen action, instead of routing the raw text. No LLM in the detection path.
- **G3** — **General, not metric-specific.** One mechanism; every offer point registers an option
  set. Wire the metric decline, person-disambiguation, and the live-search offer in this build.
- **G4** — **Drop on non-follow-up.** Any reply that is not a recognized selection clears the
  pending action and routes normally (one-shot, single turn). A new question silently supersedes.
- **G5** — **Live search uses the user's exact original wording**, never the acceptance token or a
  rewrite (this is the specific defect behind the repro garbage).
- **G6** — Fix Bug 1: structured answers are recorded in conversation history.
- **G7** — Gated rollout behind a flag; reversible.

### Non-goals (deferred — flagged, not silently dropped)
- **Offer-first live-search policy** — making the auto-firing live fallback *offer before running*
  on every KB miss. This is a user-facing **policy** change to the live-fallback trigger (adds a
  round-trip on every KB miss) and interacts with `LIVE_THRESHOLD`. Deferred to its own tightly
  scoped follow-up; once this mechanism exists it is a small swap (return an offer + pending instead
  of the auto-answer). **This build only makes the *existing* live-search offer resumable.**
- **Slot-fill offers** (#4 "This is university-wide. Just name a college.") — the reply fills an
  *arbitrary arg* rather than selecting from a bounded set; different shape, and it is the least
  broken (it already gives a real answer, then merely invites narrowing). Folds into thread F.
- **Thread F clarify content** (which bare terms to clarify, the option sets) — separate workstream;
  this build is the *infrastructure* F will plug into.

---

## 3. Architecture

Three new small units + focused wiring in `message_handler`. Layering is preserved: the **v2
retrieval layer computes what is resumable** (returns v2-native `Route`s); the **bot layer owns the
session** (writes the pending action + history) and **executes** the resume.

### 3.1 State — `bot/core/pending.py` (new)

```python
@dataclass
class PendingOption:
    label: str            # human label; also the match target for pick-1-of-N ("John Smith")
    action: str           # "structured" | "live_search"  (executor selector)
    payload: dict         # structured: {"skill": str, "args": dict}
                          # live_search: {"query": str}   (verbatim original — G5)

@dataclass
class PendingAction:
    options: list[PendingOption]
    created_at: datetime
```

- Stored on a new field `ConversationSession.pending_action: Optional[PendingAction] = None`
  (`bot/services/conversation.py`). In-memory, per `user_id`, rides the existing 60-min session
  timeout + cleanup loop. Ephemeral by design — losing it on a process restart is correct.
- **No v2 import in the session layer**: options carry plain `skill`/`args` dicts, not `Route`
  objects; the resume site rebuilds `Route(skill, args)`.
- `ConversationManager` gains `set_pending(user_id, pa)`, `get_pending(user_id) -> Optional`,
  `clear_pending(user_id)`. `clear_session` already drops the whole session (so pending too).
- The number of options drives the detector: **1 option = a yes/no offer**; **N options =
  pick-1-of-N**. No separate `kind` field needed.

### 3.2 Detector — `bot/core/followup_match.py` (new)

`match_followup(text, options) -> int | DECLINE | None` — returns the selected option index, the
`DECLINE` sentinel (explicit "no"), or `None` (no recognized selection). Fully deterministic; no LLM.

Normalize `text`: lowercase, strip leading/trailing punctuation + whitespace, collapse internal
spaces. Then:

1. **Explicit negation** (`no, nope, nah, never mind, no thanks, that's ok`) → return a sentinel
   `DECLINE` (caller acknowledges gracefully, does not route "no" as a query).
2. **Affirmation** (closed lexicon: `yes, yeah, yep, yup, sure, ok, okay, yes please, please do,
   do it, go ahead, sounds good`) **and exactly one option** → return `0`.
   - **Whole-message match only.** `"yes but what about MTSM"` has extra tokens → NOT an affirmation
     → falls through (ultimately `None` → route normally). This is the deterministic hard line.
3. **Selection among N options:**
   - **Ordinal**: `first/second/third/…`, digits `1/2/3`, `#2`, `option 2`, `the first one` →
     the (1-based) ordinal, if in range.
   - **Label match**: the normalized reply exactly equals, or is a **unique** substring of, exactly
     one option's normalized label (e.g. a surname matching one candidate). Uniqueness required.
   - If **0 or >1** options match → `None` (never guess — honors "deterministic, never guess").
4. Otherwise → `None`.

`match_followup` is a pure function → unit-testable in isolation.

### 3.3 Resumable-action computation — `v2/core/retrieval/structured_answer.py`

New: `resumable_action(rt: Route, result: dict) -> Optional[list[tuple[str, Route]]]` — given a
routed skill + its result, return the option set (label, resume-`Route`) for offer-type skills, else
`None`. Owns the single definition of "what is resumable." Returns v2-native `Route`s; the bot layer
wraps them. Initial coverage:

| Offer skill | Resume option(s) |
|---|---|
| `metric_descending_unsupported` | `[("most {noun}", Route("top_people_by_metric", {org_id, field_key, metric_key, n}))]` |
| `person_disambig` | `[(cand["name"], Route("entity_card", {"entity_id": cand["entity_id"]})) for cand in candidates]` |

**Router change (small):** the `metric_descending_unsupported` Route (`router.py:494`) currently
drops the resolved `org_id` (and `n`). Add `org_id` + `n` to its args so the resume can scope
correctly (falls back to the NJIT root org — university-wide — when `org_id` is absent, mirroring the
ascending `top_people_by_metric` default). This is the only router edit.

The live-search offer (§3.4) is registered directly by `message_handler` (its resume is
`maybe_answer_live`, not a structured skill), so it does not go through `resumable_action`.

### 3.4 Wiring — `bot/core/message_handler.py`

**(a) `_try_structured` returns text + resumable.** Change the return from `Optional[str]` to a
small `(text, resumable)` shape (`resumable` = the `list[(label, Route)]` from `resumable_action`,
or `None`). Its `_run` closure already holds both `rt` and `result`, so it computes `resumable` there
and surfaces it. Three call sites adjusted (`:250` gate-check, `:290` main, `:516` dispatch) —
None-ness/text preserved for the two that only care about answered-vs-not.

**(b) Offer early-return site (`:289-292`) — register + record.**
```
text, resumable = await self._try_structured(resolved_query)
if text is not None:
    if resumable:
        cm.set_pending(user_id, PendingAction(
            options=[PendingOption(label, "structured", {"skill": r.skill, "args": r.args})
                     for (label, r) in resumable], created_at=now))
    cm.add_turn(user_id, "user", clean_text)          # Bug 1 fix (G6)
    cm.add_turn(user_id, "assistant", text[:500])
    return MessageResponse(text=text)
```
Per G6, history is written for **every** structured answer (offer or not), matching the RAG path —
one standard.

**(c) Live-search offer becomes resumable.** Where `offer_live_search` is set true
(`message_handler.py:914`), also register a 1-option pending action whose resume is a `live_search`
on the **verbatim original query** (G5):
```
cm.set_pending(user_id, PendingAction(
    options=[PendingOption("search NJIT's website", "live_search", {"query": clean_text})],
    created_at=now))
```
The existing button is unchanged; button and typed-`"yes"` hit the same server-side resume and do
not race (different turns).

**(d) Resume pre-check — top of `handle()`** (after mode resolution ~`:226`, **before** the
context-rewrite gate `:228` and all routing). This is the entry point that makes acceptance work:
```
pending = cm.get_pending(user_id) if cm else None
if pending is not None:
    cm.clear_pending(user_id)                         # one-shot, BEFORE execute (a resume may re-offer)
    idx = match_followup(clean_text, pending.options)
    if idx is DECLINE:
        cm.add_turn(user_id, "user", clean_text)
        ack = "No problem — what else can I help you with?"
        cm.add_turn(user_id, "assistant", ack)
        return MessageResponse(text=ack)
    if idx is not None:
        resumed = await self._resume_pending(pending.options[idx])
        if resumed is not None:
            cm.add_turn(user_id, "user", clean_text)
            cm.add_turn(user_id, "assistant", resumed[:500])
            return MessageResponse(text=resumed)
    # not a recognized selection → pending already cleared → fall through, route normally (G4)
```
Pending state is **mode-agnostic** (a direct commitment); a mode-switch message is a non-selection
and drops it naturally via the fall-through.

**(e) `_resume_pending(option) -> Optional[str]`** — dispatch by `option.action`:
- `"structured"` → run `structured_answer.run(conn, Route(skill, args))` in the worker thread, then
  `format_answer` + `_compose_structured` (shared with `_try_structured` via a factored-out helper).
  **Bypasses `router.route()`** — deterministic, and sidesteps the unfixed terse-form routing gap
  (thread E).
- `"live_search"` → `await maybe_answer_live(option.payload["query"], …)` — the exact original query
  (G5). Returns its grounded answer or `None`.
Any exception, empty options, or unknown skill/action → return `None` → caller falls through to
normal routing. **Never raises into the message path.**

### 3.5 Rollout flag
`FOLLOWUP_RESUME_ENABLED` (bot config, default **off**), mirroring `ANSWER_GATE_ENABLED`. When off:
no pending actions are registered and the pre-check is skipped (pure current behavior). Owner flips
it live after review + a `restart.sh`. Backout = flag to 0 (or revert the merge).

---

## 4. The four offer/clarify points (survey)

| # | Offer point | Location | Shape | This build |
|---|---|---|---|---|
| 1 | "Want the most {noun} instead?" | `structured_answer.py:307` | yes/no | **Wired** (`resumable_action`) |
| 2 | Live-search: "search NJIT's website?" | `offer_live_search` (`:914`) | yes/no | **Wired** (`live_search` action, exact wording — G5) |
| 3 | "did you mean A, B, or C?" (person disambig) | `structured_answer.py:413-425` | pick-1-of-N | **Wired** (`resumable_action`) |
| 4 | "…university-wide. Just name a college." | `structured_answer.py:297-298` | slot-fill | **Deferred → thread F** (§2 non-goals) |

---

## 5. Data flow

**Offer turn (e.g. metric decline):** `handle()` → `_try_structured` routes
`metric_descending_unsupported`, `resumable_action` returns `[("most citations",
Route("top_people_by_metric", {org_id:ywcc,…}))]` → early-return site registers the `PendingAction`
+ writes both history turns → returns the offer text.

**Resume turn ("yes"):** `handle()` pre-check finds the pending action → clears it → `match_followup`
returns `0` (affirmation, 1 option) → `_resume_pending` runs `top_people_by_metric` scoped to YWCC
→ returns the ranked list → writes both history turns.

**Non-follow-up ("what are the office hours"):** pre-check clears the stale pending action,
`match_followup` returns `None` → falls through → routes the new question normally (G4).

---

## 6. Error handling
- Resume executes in the worker thread like `_try_structured`; any exception → `None` → fall through
  to normal routing. The message path never breaks.
- Empty option set / unknown skill / unknown action → `None` → fall through.
- Pre-check clears pending **before** executing, so a resume that itself makes an offer can register
  a fresh pending action.

---

## 7. Test matrix (TDD)

**Pure units**
- `match_followup`: `yes / Yes. / sure / ok / yes please / do it` (1 option) → `0`; `no / nope /
  never mind` → `DECLINE`; `"yes but what about MTSM"` → `None`; `""` → `None`; N-option ordinal
  (`the first`, `2`, `#3`) → index; N-option unique label/surname → index; ambiguous/0-match → `None`.
- `resumable_action`: `metric_descending_unsupported` route → correct `top_people_by_metric` option
  (org threaded); `person_disambig` → one option per candidate; any other skill → `None`.
- Router: `metric_descending_unsupported` Route now carries `org_id` + `n`.

**Integration (2-turn, one user_id)**
- **The repro now passes:** offer → `"yes"` → correct ranked YWCC-by-citations answer (add to
  `eval/questions.txt` per the grow-correctness-suite rule).
- Person disambig → `"the first"` / a surname → the right person's card.
- Live-search offer → `"yes"` → live search runs on the **verbatim original query** (assert the
  searched query == the original, not `"yes"`) (G5).
- Stale-offer superseded: offer → unrelated question → pending cleared, normal answer, no resume.
- `"yes but …"` after an offer → NOT resumed → routed normally.
- Decline: offer → `"no"` → graceful ack, pending cleared, not routed as a query.
- History recorded after a structured answer (offer **and** plain) — Bug 1 / G6.
- Expiry: pending does not survive two messages.
- **Regression:** a genuine referential follow-up after a plain structured answer still resolves
  correctly now that structured answers appear in history (context-rewrite interaction — see §8).
- Flag off → zero behavior change (no pending registered, pre-check skipped).

---

## 8. Risk: recording all structured answers in history (G6)

Today no structured answer reaches conversation history. This build writes **every** structured
answer to history (one standard, no bandaid). Consequence: `context_rewrite` will now see structured
answers as prior context and *may* fire `is_follow_up` on a referential follow-up where it currently
cannot. This is arguably an improvement (structured answers become first-class conversational turns),
but it is a behavior change. **Mitigation:** the §7 regression test asserts referential follow-ups
after a structured answer still behave. If review finds the surface too wide, the fallback is to
record history only at offer sites (narrower, but two standards) — flagged here for the reviewer's
call. **Recommended: record all (current design).**

---

## 9. Goals checklist (shipped / deferred)

| Goal | Status |
|---|---|
| G1 pending-action state + offer-turn recorded | Shipped in this build |
| G2 deterministic reply→option match + execute | Shipped |
| G3 general (metric #1, disambig #3, live #2 wired) | Shipped |
| G4 drop on non-follow-up (one-shot) | Shipped |
| G5 live search uses exact original wording | Shipped |
| G6 structured answers recorded in history | Shipped (risk §8) |
| G7 gated rollout flag `FOLLOWUP_RESUME_ENABLED` | Shipped |
| Offer-first live-search policy (#a) | **Deferred → own follow-up** (§2) |
| Slot-fill offer #4 ("name a college") | **Deferred → thread F** (§2) |
| Thread F clarify content | **Deferred → thread F** (this is its infra) |

---

## 10. Review gate
Per the EXPERT-REVIEW HARD GATE: this design goes to a senior-engineer review **and** a RAG/LLM
review (it touches the answer/retrieval path), plus a Codex second opinion, then owner approval,
before any build. Build is TDD; diff shown before commit + `restart.sh`.

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
- **G3** — **General, not metric-specific.** One mechanism; every wired offer point registers an
  option set. Wire the **metric decline (#1)** and **person-disambiguation (#3)** in this build.
  (Live-search #2 is intentionally NOT wired — see below.)
- **G4** — **Drop on non-follow-up.** Any reply that is not a recognized selection clears the
  pending action and routes normally (one-shot, single turn). A new question silently supersedes.
- **G5** — **A mode switch clears everything.** Switching conversation mode (gsa⇄free, or into a
  judging mode) wipes the session — conversation context AND any pending action. Pending is NOT
  carried across a mode switch. (Owner decision 2026-07-03; also resolves the "resume GSA content in
  free mode" boundary.)
- **G6** — Fix Bug 1: structured answers are recorded in conversation history.
- **G7** — Gated rollout behind a flag; reversible.

### Non-goals (deferred — flagged, not silently dropped)
- **Live-search wired into the pending mechanism (#2)** — the live-search **button** is an
  independent, always-available override the user can tap at any turn; it is deliberately kept
  OUTSIDE this mechanism (owner decision 2026-07-03). A button tap runs live search and **clears any
  pending action** (it overrides). Because the button only ever appears on RAG deflections (which set
  no pending), there is provably no double-fire. Making live search *offer-first* and resumable via a
  typed "yes" (with the exact-original-wording payload) is a **separate follow-up** — the owner had
  already suggested deferring it ("if these are too much, put it for follow up"). The long-term aim
  is a bot good enough that users rarely reach for the button at all.
- **Offer-first live-search policy** — making the auto-firing live fallback *offer before running*
  on every KB miss. User-facing **policy** change to the live-fallback trigger (adds a round-trip on
  every KB miss) and interacts with `LIVE_THRESHOLD`. Part of the same deferred live-search follow-up.
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
    action: str           # "structured"  (only executor wired this build; kept as a field so the
                          #  deferred live-search follow-up can add "live_search" with no reshape)
    payload: dict         # structured: {"skill": str, "args": dict}

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
- **Mode switch clears everything (G5):** `set_mode` already routes through the shared
  `ConversationModeStore`. `ConversationManager.set_mode` will, on an *actual* mode change, call
  `clear_session(user_id)` — wiping conversation history AND pending. (No-op when the mode is
  unchanged.) This is the single enforcement point for "a mode switch loses everything."
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

New: `resumable_action(rt: Route) -> Optional[list[tuple[str, Route]]]` — given a routed skill,
return the option set (label, resume-`Route`) for offer-type skills, else `None`. Reads ONLY `rt`
(skill + args) — a `person_disambig`'s candidates live in `rt.args["candidates"]`, so no `result` is
needed. (This is what lets the chokepoint avoid changing any existing return contract — see §3.4.)
Owns the single definition of "what is resumable." Returns v2-native `Route`s; the bot layer wraps
them. Initial coverage:

| Offer skill | Resume option(s) |
|---|---|
| `metric_descending_unsupported` | `[("most {noun}", Route("top_people_by_metric", {org_id, field_key, metric_key, n}))]` |
| `person_disambig` | `[(cand["name"], Route("entity_card", {"entity_id": cand["entity_id"]})) for cand in candidates]` |

**Router change (small):** the `metric_descending_unsupported` Route (`router.py:494`) currently
drops the resolved `org_id` (and `n`). Add `org_id` + `n` to its args so the resume can scope
correctly (falls back to the NJIT root org — university-wide — when `org_id` is absent, mirroring the
ascending `top_people_by_metric` default). This is the only router edit.

**Reachability to verify at build:** in production `ROUTER_V21=1`, so KG answers flow through the
UnifiedRouter → `_answer_decision` (§3.4), not the legacy `_try_structured`. Both call
`structured_answer.run()`, so `resumable_action(rt, result)` is invoked at the shared chokepoint
regardless of router. The build must confirm the UnifiedRouter actually emits `person_disambig` as a
KG skill (it wraps `router.route()`, which can return that Route) — if a fuzzy person instead
degrades to the generic CLARIFY→RAG path under Phase-1b, wiring #3 lands only once disambiguation is
a real KG outcome. Metric-decline (#1) is confirmed reachable on the live path (the repro produced it
under `ROUTER_V21=1`).

### 3.4 Wiring — `bot/core/message_handler.py`

> **CRITICAL (finding S0):** production runs `ROUTER_V21=1, SHADOW=0` — KG answers flow through the
> **UnifiedRouter → `_answer_decision`** (`:535-551`, via `_structured_from_route` `:499`), which
> **short-circuits at `:278` and never reaches `_try_structured` (`:290`)**. `_try_structured` runs
> only on the `ROUTER_V21=0` kill-switch. Both paths call `structured_answer.run()` +
> `_compose_structured`, and both currently return early *before* the `:920` history write (Bug 1 on
> BOTH). So the wiring must live at a **shared chokepoint**, not at either return site individually.

**(a) Shared chokepoint helper — the single place pending + history are written.**
Introduce `_register_and_record(user_id, clean_text, rt, text) -> None` on the handler — a
**side-effect** (no return; the caller builds its own `MessageResponse`). Given the routed skill
(`rt`) and the already-composed answer `text`, it:
1. `resumable = structured_answer.resumable_action(rt)`;
2. if `resumable` (and the flag is on), `cm.set_pending(user_id, PendingAction(options=[PendingOption(
   label,"structured",{"skill":r.skill,"args":r.args}) for (label,r) in resumable], created_at=now))`;
3. writes history for **every** structured answer (offer or not) — `add_turn(user,clean_text)` +
   `add_turn(assistant, text[:500])` — the **Bug 1 fix (G6)**, one standard with the RAG path.

**Both** structured return sites call it: `_answer_decision`'s KG branch (`:548-551`, the LIVE path)
and `_try_structured` (`:290`, the kill-switch path). Because `resumable_action` needs only `rt`, the
chokepoint takes a `Route` — NOT `result` — so **`_structured_from_route` and `_try_structured` keep
their exact current return contracts** (3-tuple / `Optional[str]`) and every existing mock stays
valid. `_answer_decision` builds a `Route` from `decision.skill`/`decision.args`; `_try_structured`
surfaces `rt` from its internal `_run` closure and registers only when the `:290` caller passes a
`user_id` (the `:250`/`:516` probe calls pass none → no registration, unchanged behavior).

**(b) Resume pre-check — top of `handle()`** (right after mode resolution ~`:226`, **before** the
context-rewrite call at `:228`, `_answer_decision`, and all routing). This is the entry point that
makes acceptance work, and it must precede context-rewrite so `"yes"` is never rewritten against the
freshly-recorded offer turn:
```
pending = cm.get_pending(user_id) if cm else None
if pending is not None:
    cm.clear_pending(user_id)                         # one-shot, BEFORE execute (a resume may re-offer)
    idx = match_followup(clean_text, pending.options)
    if idx is DECLINE:                                # explicit "no"
        cm.add_turn(user_id, "user", clean_text)
        ack = "No problem — what else can I help you with?"
        cm.add_turn(user_id, "assistant", ack)
        return MessageResponse(text=ack)
    if idx is not None:                               # a selection WAS recognized
        resumed = await self._resume_pending(pending.options[idx])
        if resumed is not None:
            cm.add_turn(user_id, "user", clean_text)
            cm.add_turn(user_id, "assistant", resumed[:500])
            return MessageResponse(text=resumed)
        # recognized but execution FAILED → graceful stop; NEVER fall through to route "yes"/"the first"
        cm.add_turn(user_id, "user", clean_text)
        sorry = "Sorry — I couldn't pull that up just now. Could you ask again?"
        cm.add_turn(user_id, "assistant", sorry)
        return MessageResponse(text=sorry)
    # idx is None → NO selection recognized → pending already cleared → fall through, route normally (G4)
```
**Finding #1 (Fable, HIGH) is fixed here:** a *recognized* selection whose execution returns `None`
returns a graceful message — it does **not** fall through and route the raw `"yes"`/`"the first"`
(which would resurrect the exact garbage this fix exists to kill). Only an *unrecognized* reply
(`idx is None`) falls through. Pending is cleared regardless (one-shot).

Pending is **not** carried across a mode switch — the mode-switch → `clear_session` rule (§3.1, G5)
wipes it, so there is no "resume in the wrong mode" case.

**(c) `_resume_pending(option) -> Optional[str]`** — dispatch by `option.action` (only `"structured"`
this build):
- `"structured"` → run `structured_answer.run(conn, Route(skill, args))` in the worker thread, then
  `format_answer` + `_compose_structured` (shared helper). **Bypasses `router.route()`** —
  deterministic, and sidesteps the unfixed terse-form routing gap (thread E). Any exception / empty /
  unknown skill → `None` (caller → graceful stop, per (b)).

**(d) Live-search stays OUT of this mechanism** (owner decision — §2 non-goals). The `offer_live_search`
bool and its button are unchanged. **One defensive addition:** the connector's live-search **button
callback** calls `cm.clear_pending(user_id)` before running the search, so a tap always overrides any
pending state. (There is no coexistence in practice — the live button appears only on RAG deflections,
which set no pending — so this is belt-and-suspenders honoring "the button overrides anything.")
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
| 2 | Live-search: "search NJIT's website?" | `offer_live_search` (`:914`) | yes/no | **Deferred** — button stays an independent override; button-tap clears pending (§3.4d, §2) |
| 3 | "did you mean A, B, or C?" (person disambig) | `structured_answer.py:413-425` | pick-1-of-N | **Wired** (`resumable_action`; reachability §3.3) |
| 4 | "…university-wide. Just name a college." | `structured_answer.py:297-298` | slot-fill | **Deferred → thread F** (§2 non-goals) |

---

## 5. Data flow

**Offer turn (e.g. metric decline):** `handle()` → UnifiedRouter (`ROUTER_V21=1`) → `_answer_decision`
KG branch runs `metric_descending_unsupported` → **`_finalize_structured`** computes
`resumable_action` = `[("most citations", Route("top_people_by_metric", {org_id:ywcc,…}))]`,
registers the `PendingAction`, writes both history turns, returns the offer text.

**Resume turn ("yes"):** `handle()` pre-check finds the pending action → clears it → `match_followup`
returns `0` (affirmation, 1 option) → `_resume_pending` runs `top_people_by_metric` scoped to YWCC
→ returns the ranked list → writes both history turns.

**Non-follow-up ("what are the office hours"):** pre-check clears the stale pending action,
`match_followup` returns `None` → falls through → routes the new question normally (G4).

**Mode switch mid-offer:** offer turn sets pending → user types "free mode" → `set_mode` detects a
real change → `clear_session` wipes context + pending (G5) → the message routes as a fresh free-mode
turn; a later "yes" has nothing to resume.

---

## 6. Error handling
- Resume executes in the worker thread like `_try_structured`; any exception → `_resume_pending`
  returns `None`. The message path never breaks (no raise escapes).
- **Recognized selection, failed execution** (`idx` valid but `resumed is None`) → a graceful "Sorry,
  I couldn't pull that up" message. It **never** falls through to route the raw acceptance token
  (finding #1). Only an *unrecognized* reply (`idx is None`) falls through to normal routing.
- Empty option set / unknown skill → `None` → graceful stop (a selection was recognized).
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

**Integration (2-turn, one user_id) — run with `ROUTER_V21=1` (the live path)**
- **The repro now passes:** offer → `"yes"` → correct ranked YWCC-by-citations answer, via
  `_answer_decision` (assert it does NOT go through `_try_structured`) (add to `eval/questions.txt`
  per the grow-correctness-suite rule).
- Person disambig → `"the first"` / a surname → the right person's card.
- **Recognized-but-failed (finding #1):** offer → `"yes"` but `_resume_pending` returns `None`
  (monkeypatch to force it) → the **graceful "couldn't pull that up" message**, and assert the raw
  `"yes"` is NOT routed (no live-search / no random page).
- Stale-offer superseded: offer → unrelated question → pending cleared, normal answer, no resume.
- `"yes but …"` after an offer → NOT resumed → routed normally.
- Decline: offer → `"no"` → graceful ack, pending cleared, not routed as a query.
- **Mode switch (G5):** offer → `"free mode"` → pending AND history cleared; a following `"yes"`
  resumes nothing (routes as fresh free-mode input).
- **Live-search button override:** with a pending action set (hypothetical), a button-tap callback
  clears pending (assert `get_pending is None` after the tap).
- History recorded after a structured answer (offer **and** plain) — Bug 1 / G6 — on the
  `_answer_decision` path.
- Expiry: pending does not survive two messages.
- **Regression (context-rewrite, finding #3):** (a) a genuine referential follow-up after a plain
  structured answer still resolves correctly; **(b) a NON-referential new query after a structured
  answer is NOT wrongly rewritten** (no false-positive `is_follow_up`); (c) a long ranked-list answer
  truncated at `[:500]` does not corrupt a downstream referential rewrite (partial-name breakage).
- Flag off → zero behavior change (no pending registered, pre-check skipped).

---

## 8. Risk: recording all structured answers in history (G6)

Today no structured answer reaches conversation history. This build writes **every** structured
answer to history (one standard, no bandaid). **Bounded by the existing rolling cap:** `add_turn`
already keeps only the last `max_turns * 2` messages (default **last 10**), popping the oldest — so
"record all" means "record all *within the existing last-10 window*," never unbounded growth. It also
means an offer that ages out of that window is fine: **the resume reads the separate `pending_action`
slot, not the transcript**, so history is only about context-rewrite continuity, never the resume
itself. Consequence: `context_rewrite` will now see structured answers as prior context and *may*
fire `is_follow_up` on a referential follow-up where it currently cannot — bounded to those last-10
turns. This is arguably an improvement (structured answers become first-class conversational turns),
but it is a behavior change. Two concrete hazards (finding #3):
- **False-positive rewrite:** a new, non-referential question after a structured answer must not be
  wrongly rewritten. Covered by §7 regression (b).
- **`[:500]` truncation:** long ranked-list / roster answers are stored truncated. A downstream
  referential rewrite that keys off a name cut mid-string could break. Covered by §7 regression (c);
  if it bites, store a rewrite-friendly summary instead of a raw truncation.

**Mitigation:** the expanded §7 regression set (a/b/c). If review still finds the surface too wide,
the fallback is to record history only at offer sites (narrower, but two standards) — flagged for the
reviewer's call. **Recommended: record all (current design).**

---

## 9. Goals checklist (shipped / deferred)

| Goal | Status |
|---|---|
| G1 pending-action state + offer-turn recorded | Shipped in this build |
| G2 deterministic reply→option match + execute | Shipped |
| G3 general (metric #1 + disambig #3 wired via shared chokepoint) | Shipped |
| G4 drop on non-follow-up (one-shot) | Shipped |
| G5 mode switch clears everything (context + pending) | Shipped |
| G6 structured answers recorded in history (both router paths) | Shipped (risk §8) |
| G7 gated rollout flag `FOLLOWUP_RESUME_ENABLED` | Shipped |
| Live-search #2 wired into pending (typed-yes / offer-first / exact-wording) | **Deferred → own follow-up** (§2) |
| Slot-fill offer #4 ("name a college") | **Deferred → thread F** (§2) |
| Thread F clarify content | **Deferred → thread F** (this is its infra) |
| Finding S0 — wiring targets the live `_answer_decision` path (chokepoint) | Addressed (§3.4) |
| Finding #1 — recognized-but-failed → graceful stop | Addressed (§3.4b, §6) |

---

## 10. Review gate
Per the EXPERT-REVIEW HARD GATE, this design is reviewed before any build. **Owner chose a single
Fable review this round** (instead of the usual senior-eng + RAG + Codex panel). Fable's review +
main's own code-path investigation are logged in §11. Next: owner approval → build TDD → diff shown →
sign-off → commit + `restart.sh`.

## 11. Review log (2026-07-03)

**Main (code-path investigation) — finding S0 (highest):** the original draft anchored all wiring to
`_try_structured`, but production runs `ROUTER_V21=1` → answers flow through `_answer_decision`;
`_try_structured` is bypassed. Empirically confirmed by the repro (offer produced under
`ROUTER_V21=1`, history empty). **Resolved:** shared `_finalize_structured` chokepoint covering both
router paths (§3.4a).

**Fable review — dispositions:**
- **#1 (HIGH) — recognized-but-failed execution routed the raw token.** Fixed: graceful stop, never
  fall through when a selection was recognized (§3.4b, §6).
- **#2 (HIGH) — live-search button double-fire.** Resolved by **de-scoping live-search** from the
  mechanism (owner decision); button stays an independent override and clears pending on tap (§3.4d,
  §2). No pending/​button coexistence remains.
- **#3 (MED) — §8 regression too thin.** Expanded to false-positive `is_follow_up` + `[:500]`
  truncation hazard (§7 regression a/b/c, §8).
- **#4 (MED) — pending across a mode switch.** Resolved by the owner's "mode switch loses everything"
  rule — `set_mode` → `clear_session` (§3.1 G5); no cross-mode resume exists.
- **#5 (LOW) — `"yes"` to a pick-1-of-N dead-ends.** Accepted as known UX (correct per
  "never guess"); a bare `"yes"` to an N-option "did you mean…?" clears + routes normally.
- **#6 (LOW) — casing/TOCTOU.** `clean_text` preserves original casing; the get→clear TOCTOU on
  concurrent double-taps is benign at this cadence. Noted.

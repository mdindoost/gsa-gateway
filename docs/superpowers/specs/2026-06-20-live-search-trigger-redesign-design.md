# Live-search trigger redesign — design

**Date:** 2026-06-20
**Status:** DESIGN v2 — both expert reviews IN + folded in; awaiting Mohammad's approval to build (TDD)
**Supersedes the trigger half of:** `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md`
(the extractive engine `bot/core/live_fallback.py` + `v2/integration/njit_search.py` is UNCHANGED;
this redesigns only *when/how* it fires and is offered).

## Problem

The live njit.edu fallback (`maybe_answer_live`) currently fires on exactly one signal: a **genuine
KB miss** — `not chunks` or top reranker `relevance < LIVE_THRESHOLD` (0.15), in
`message_handler._rag_pipeline` (≈line 452–466). That single auto-fire trigger has two gaps:

1. **The confident-deflection hole (the main bug).** When chunks exist and score *above* 0.15 but
   don't actually contain the answer, the LLM composes a deflection that *reads as answered* —
   e.g. "The library has study spaces… for current hours, see library.njit.edu." Auto-fire never
   trips (relevance was high enough) and the user never taps 👎/🔄 (it looked answered), so the
   live search that could have fetched the real answer **never fires**. This is the exact failure
   the feature exists to fix, and it's silent.

2. **No user-initiated path.** A user who knows the KB is thin can't say "search NJIT for X", and a
   user who got a deflection or a bad answer has no affordance that says "go look it up live."

An earlier idea — an LLM "should we go live?" sentinel — is **dropped** (both reviewers): too
expensive/unreliable for a per-message gate, and it duplicates signals we already have for free.

## Goals

1. Keep the 0.15 auto-fire floor for genuine misses (no chunk / off-topic) — disjoint path, unchanged.
2. Close the confident-deflection hole with a **proactive offer** (not auto-fire) on detected deflections.
3. Add **user-initiated** live search via contextual offers at the moments where it's relevant, plus
   an explicit "search njit for X" command path.
4. Preserve every extractive guarantee (verbatim, page-grounded spans only; no hallucination).
5. Sharpen framing so a live answer reads as **live, verbatim, from a different source**, and give an
   explicit "searched but found nothing" message when a *user-triggered* search comes back empty.
6. Stay **dormant-by-default** in the UI — no permanent paid-affordance button on every answer.
7. Degrade gracefully when the feature is off (`LIVE_ENABLED=0` or no `BRAVE_API_KEY`): no offers shown.

Non-goals (explicitly deferred): free-text "that's wrong" / "no that's incorrect" NLU (too noisy; a
bare "no" is usually a real conversational turn — defer to v1).

## Decisions (settled in review synthesis, approved by Mohammad 2026-06-20)

- **Drop the LLM sentinel.**
- **No permanent 🌐 button.** Surface a *contextual* web-search offer only where relevant.
- **Include the proactive deflection-offer** (Mohammad chose Option A) — closes the silent hole via a
  cheap, offer-only (never auto-fire) deflection signal, so a false positive is harmless.
- **👎 routing:** stays through the existing "what was wrong?" detail step; only **Wrong info** /
  **Incomplete** attach the web-search offer (NOT Off topic).
- **🔄 same-answer dead-end** is the escalation moment — attach the offer there.
- **Explicit "search njit for X"** ships (best ROI) — direct path, no offer.

## Trigger set (the whole surface, after this change)

| Situation | Behavior |
|---|---|
| Genuine miss (no chunk / `relevance < 0.15`) | **auto-fire** live search (unchanged) |
| Detected deflection (answered-looking but no real answer) | **contextual offer** → user taps → live search |
| 🔄 → "same answer" dead-end | **contextual offer** → tap → live search |
| 👎 → "Wrong info" / "Incomplete" | **contextual offer** → tap → live search |
| Explicit "search njit(.edu) for X" / "look up X on njit" | **direct** live search, no offer |
| 👎 → "Off topic", tone complaints, bare "no" | no offer (unchanged) |

## Deflection detection (the new signal — offer-only, so safe)

Two complementary detectors, both producing a boolean `is_deflection` carried out of `_rag_pipeline`.
**Tag-at-source is PRIMARY** (zero precision cost); prose-matching is a NARROW secondary net.

1. **Tag-at-source (authoritative, primary).** Our own canned no-info deflection (message_handler line
   ≈497–505) is set with `is_deflection=True` directly — we wrote it, we know exactly when it fires.

2. **Narrow phrase-match on LLM prose (secondary).** After `generate_answer` produces an answer from
   chunks, run a small, case-insensitive matcher over the **pre-heads-up** `response_text` (RAG-S4 /
   senior-S4: the heads-up line itself says "confirm with <office>" and would self-trigger — match
   BEFORE `apply_headsup`). Anchored to the deflection *gesture* (pointing the user elsewhere FOR the
   answer), NOT to any mention of a contact:

   ```python
   # bot/core/deflection.py — module-level pre-compiled (re.compile), one constant.
   DEFLECTION_TELLS = [
       # "for the {current/latest/exact/more} …, see/visit/check …" — the canonical gesture
       r"for (?:the )?(?:current|latest|up[- ]?to[- ]?date|most recent|exact|specific|detailed|more|further)\b[^.]*?\b(?:see|visit|check|refer to|go to|consult)\b",
       r"\b(?:please )?(?:see|visit|check|refer to|consult)\b[^.]*?\b\S+\.njit\.edu",   # "see X.njit.edu"
       r"\b(?:you (?:can|should|may)|i(?:'d| would) (?:recommend|suggest)) (?:check|visit|see|look at)\b[^.]*?\b(?:website|page|site)\b for (?:the )?(?:current|latest|exact|more|specific)\b",
       # explicit no-info admissions (belt-and-suspenders vs tag-at-source)
       r"\bi (?:don't|do not|wasn't able to|was not able to|couldn't|could not) (?:have|find|locate)\b",
       r"\bi (?:don't|do not) have (?:that|specific|detailed|the exact|enough) (?:information|details|data)\b",
       r"\bnot (?:available|listed|specified|included) in (?:the|our|my|gsa'?s?) (?:knowledge base|kb|records|data)\b",
   ]
   ```
   Pure function `looks_like_deflection(text) -> bool` in `bot/core/deflection.py`, unit-tested.
   - **Precision moves (RAG-S1):** every "see/visit/check" tell requires a volatile qualifier
     ("current/latest/exact/more…") OR an explicit `*.njit.edu` target — that distinguishes "for current
     hours, see library.njit.edu" (deflection) from "see his website" / "see the syllabus" in a bio (not
     a deflection). **"contact"/"reach out" are deliberately NOT tells** — honest-partial + heads-up
     answers (the project's *correct, best* behavior) route to an office on purpose and must NOT draw an
     offer. A contact-only dead end still has 👎→Incomplete to escalate.
   - **Risk posture (RAG-S2):** offer-only means we favor RECALL over precision, but NOT *loose* — a
     false positive is cheap (an extra button) but not free (it reframes a correct answer as suspect AND
     a tap spends a Brave credit against the ~1,000/mo cap). Tune for high recall on the shapes we
     actually emit, with the cheap precision wins above. A false negative = status quo.

`is_deflection` is set in `_rag_pipeline` = (canned-deflection branch) OR
(`looks_like_deflection(pre_headsup_text)` when answered from chunks). It is suppressed (forced False)
when: the feature is off, `used_live` is True (we just answered live), OR **`attempted_live` is True**
(the auto-fire path ran this turn and returned None — don't offer to redo a search that just failed;
RAG-S3 / senior-S1). `attempted_live` is a new local flag set whenever the auto-fire block is entered,
distinct from `used_live` (entered-and-succeeded).

## Carrying the signal: MessageResponse

Add one field:

```python
@dataclass
class MessageResponse:
    ...
    question_id: Optional[int] = None
    offer_live_search: bool = False   # NEW — connector should attach a "search NJIT" offer
```

`offer_live_search` is the single connector-facing flag. `_rag_pipeline` sets it = `is_deflection`
(gated by feature-on). The connector decides the *button* presentation; the handler decides *whether*
an offer is warranted. Keeps the deflection heuristic platform-independent and unit-testable in the
handler, not the connector.

## Connector plumbing (Telegram first; Discord parity tracked)

All live-search re-issue needs is the original `question_text`, which `_pending_feedback[qid]` already
stores. We add ONE new callback verb and ONE new handler.

**New callback:** `web:{question_id}` (fits the 64-byte limit; far shorter than `fbd:` payloads).
Registered with `CallbackQueryHandler(self._on_web_search, pattern=r"^web:\d+$")`.

**Pending-entry lifetime is the crux** (both reviews — senior-B1/B2/B3, RAG-S4/S5). `_on_web_search`'s
ownership check reads `_pending_feedback[qid]["user_id_hash"]` and needs `question_text` there, so any
path that shows a web button MUST guarantee a pending entry for that qid survives to the tap.
**Decision: make `_register_pending` unconditional whenever `resp.question_id` is set** (drop the
`if keyboard:` coupling, senior-B1), and adjust the two button-press paths below.

**Where the offer button is attached:**
- **Proactive deflection** — when `resp.offer_live_search` is True, `_build_feedback_keyboard` adds a
  second row `[🌐 Search NJIT's website]` (`web:{qid}`) beneath the normal 👍/👎/🔄 row. The normal
  `_register_pending` (now unconditional) already stores `question_text` for this qid. ✅ works as-is.
- **🔄 same-answer dead-end** (line ≈351–356) — the "I got the same answer" text gets a
  `[🌐 Search NJIT's website]` keyboard. **Required (NOT either/or): read `question_text` first (already
  captured in the local at line 302) AND re-register a minimal pending entry under the ORIGINAL qid**
  immediately before attaching the button — because line 304 already popped it, so `_on_web_search`'s
  ownership lookup would otherwise hit `pending is None`. (senior-B2 / RAG-S4)
- **👎 → detail** — **stop popping the entry in the `down` branch (line 293); defer the pop to
  `_on_feedback_detail`** so `question_text` survives for the web re-issue (senior-B3 / RAG-S5). In
  `_on_feedback_detail`, if `detail in {"wrong_info","incomplete"}`, change the terminal
  `edit_message_text("✅ Feedback recorded")` to carry `reply_markup=[🌐 Search NJIT's website]`
  (web:{original_qid}); pop the entry only on the `off_topic` branch / after the offer is shown. (As
  specced originally the offer was DEAD — the entry was already popped.)

**`_on_web_search(update, context)`** (new):
1. Parse `web:{qid}`; **edit the offer button away FIRST** (before any await-heavy search — double-tap
   guard, senior-B2). Ownership check against `_pending_feedback[qid]["user_id_hash"]`. If the entry
   expired/missing, polite "ask me again" fallback (we no longer have the question text).
2. Post a "🌐 Searching NJIT's website…" placeholder (cleaned up / edited on result, incl. on None).
3. Call the shared seam `MessageHandler.live_search(question_text) -> Optional[LiveAnswer]`.
4. On a `LiveAnswer`: render `live.text` + apply heads-up. On `None`: the shared "found nothing"
   message constant (Goal 5; one constant, N3).

**`MessageHandler.live_search(question)` seam (senior-S2).** ONE method wrapping `maybe_answer_live`
with the injected `brave_search`/`http_fetch`/`generate` lambda. **Refactor the existing inline auto-fire
call (message_handler.py:452–466) to go through this same seam** so the provider wiring + the
`generate(user, system)` arg-order live in exactly one place (no drift). The seam itself hard-gates:
returns None unless `LIVE_ENABLED and BRAVE_API_KEY and self.ollama` — so a stale on-screen button
tapped after the key was pulled degrades to "found nothing", never a crash (failure-mode F1 / G7).

**Discord:** the same `offer_live_search` flag + a button is needed in `bot/commands/chat.py`'s view.
Tracked as a build sub-task; confirm button/view parity there before claiming the goal done. (Memory:
buttons = RAG answers only; structured/entity answers stay button-free — the offer must respect that
same gating.)

## Explicit "search njit for X" path

A new intent in the router/intent layer: a phrase match for `search njit(.edu)? for X`,
`look (X )?up on njit`, `check njit('s site)? for X` → extract `X` → call
`MessageHandler.live_search(X)` directly (bypass KB), render the LiveAnswer or the
"searched, found nothing" message. Deterministic phrase trigger (NOT free-text NLU), safe to auto-run
because the user explicitly asked to go live. **Ordering (N4): the explicit-search intent wins and
short-circuits BEFORE both the structured KG router and the RAG pipeline** — if the user literally typed
"search njit for X" they want the live web, not the entity card (documented, intentional). **Button
treatment (senior-S3):** the live answer is logged with a `question_id` and gets the normal 👍/👎/🔄
keyboard for consistency with today's auto-fire live answers, but NO web-re-search row (it just searched).

## Framing (Goal 5)

`live_fallback._format` prefixes "From NJIT's website:" and appends "Source: <url>". Sharpen the prefix
to make the live/verbatim/different-source nature unmistakable — e.g. "🌐 Live from NJIT's website
(fetched live):" (RAG-N3: "fetched live", NOT "just now" — the auto-fire path may serve a cached
`raw_pages` fetch, so avoid the freshness overclaim). The "searched, found nothing" copy lives in ONE
shared constant (N3) and fires on the **user-triggered** empties only (auto-fire empties stay on today's
deflection — don't announce a failed search the user didn't ask for). This is the one engine-adjacent
edit: engine *logic* is unchanged; only the `_format` copy string is sharpened (senior-N4).

## What is NOT changing

- `bot/core/live_fallback.py` extractive engine, `ground_spans`, the Brave provider isolation.
- The 0.15 auto-fire path and `LIVE_ENABLED`/`BRAVE_API_KEY` gating / kill-switch.
- Heads-up application on live answers.
- Free-text "that's wrong" handling (deferred).

## Testing (TDD)

- `bot/core/deflection.py::looks_like_deflection` — positive samples (canned no-info text, "for current
  hours, see library.njit.edu", "I don't have that information") → True; AND **negative samples that MUST
  be False:** a heads-up/honest-partial answer ("confirm with the ISSS office", "N of the faculty list
  research areas"), a faculty bio "see his website" / "see the syllabus", a normal factual answer. The
  negatives are as important as the positives (RAG build-must-do #5).
- `_rag_pipeline` sets `offer_live_search` correctly: genuine answer → False; canned deflection → True;
  phrase-match deflection → True; feature-off → False; `used_live` → False; **`attempted_live` (auto-fire
  ran, returned None) → False** (S1/S3).
- `MessageHandler.live_search` seam returns the LiveAnswer / None passthrough (mock `maybe_answer_live`),
  and returns None when the feature is off (F1/G7); the auto-fire path now routes through it (no drift).
- Telegram: `_on_web_search` ownership check; expired-pending fallback; empty-result message; double-tap
  guard (button edited away FIRST). **🔄 dead-end re-registers a pending entry; 👎 `down` no longer pops,
  `_on_feedback_detail` carries the web button on wrong/incomplete and pops after** (B2/B3/S4/S5).
  Explicit-search intent extraction (`search njit for X` → X).
- Grow `eval/questions.txt` with the library-hours-style confident-deflection case, an explicit
  "search njit for X" case, AND a contact-routing/heads-up answer as a NEGATIVE (must NOT draw an offer).

## Goals checklist — BUILD RESULT (2026-06-20)

- [x] **G1** 0.15 auto-fire unchanged — control flow intact; now routes through the `live_search` seam.
- [x] **G2** proactive deflection-offer — `bot/core/deflection.py` (tag-at-source + narrow phrase-match).
- [x] **G3** contextual offers (deflection / 🔄-dead-end / 👎-wrong+incomplete) + explicit "search X" — **Telegram**.
- [x] **G4** extractive guarantees preserved — engine logic untouched (only `_format` copy sharpened).
- [x] **G5** sharpened framing ("🌐 Live from NJIT's website (fetched live):") + shared `LIVE_NOT_FOUND_MSG`.
- [x] **G6** no permanent button — offers are contextual, gated on `offer_live_search` / feature-on.
- [x] **G7** graceful degrade when off — gated in handler AND in the `live_search` seam.
- [ ] **Discord parity — DEFERRED, loudly flagged.** Handler-level wins (explicit "search njit for X", the
      `live_search` seam, the `offer_live_search` flag) work on BOTH platforms; only the offer *button*
      rendering is Telegram-only. `bot/commands/chat.py` ignores `offer_live_search` (harmless — no error;
      Discord users just don't see the offer). Follow-up: add the button to Discord's `FeedbackView`.
- [ ] **Free-text NLU — DEFERRED** (stated non-goal).

**Tests:** 44 new across `test_deflection`, `test_offer_live_search`, `test_live_search_seam`,
`test_live_query`, `test_explicit_search_handle`, `test_telegram_web_search`, `test_live_fallback` — all
green. Full suite 1140 passed / 13 failed; all 13 confirmed PRE-EXISTING on clean `main` — none mine.

## Open questions — RESOLVED by review (2026-06-20)

1. **Phrase-match precision vs recall** → RESOLVED: tag-at-source primary; prose-matching narrow
   (volatile-qualifier/`*.njit.edu`-anchored), "contact"/"reach out" dropped. Favor recall, not loose.
2. **Pending-entry lifetime** → RESOLVED: `_register_pending` unconditional on `question_id`; 🔄
   re-registers; 👎 `down` defers its pop to the detail handler. (Both reviews confirmed the pop-before-
   offer bug is real.)
3. **Discord parity** → DEFERRED, loudly flagged (tracked sub-task; the `offer_live_search` flag is
   harmless when `chat.py` ignores it — Discord users simply don't see the offer, no error).
4. **Auto-fire ↔ offer disjointness** → RESOLVED: disjoint in outcome; the one overlap (no-chunk →
   auto-fire ran but returned None → reach canned deflection) is handled by the `attempted_live`
   suppression so we never offer to redo a search that just failed this turn.

## Review outcomes folded in (2026-06-20)

Senior-eng verdict: **ship-with-fixes** (3 lifecycle bugs + 2 should-fixes). RAG verdict: **approve with
changes** (no blockers; matcher precision + disjointness). Both confirmed: anti-fabrication/extractive
guarantees untouched, keep the 0.15 floor, no goal silently dropped. All findings above are folded into
this revision: tightened DEFLECTION_TELLS (drop contact/reach-out), `attempted_live` suppression,
pre-heads-up matching, unconditional `_register_pending`, 🔄 re-register, 👎-pop deferral + detail button,
single `live_search` seam with the feature-gate inside it, "fetched live" framing, shared "found nothing"
constant, explicit-path ordering + button treatment, and the negative-sample tests.

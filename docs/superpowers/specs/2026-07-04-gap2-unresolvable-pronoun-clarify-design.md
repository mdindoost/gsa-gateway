# Design — Gap #2: clarify when a pronoun is genuinely UNRESOLVABLE

**Date:** 2026-07-04 · **Author:** Claude (Opus) · **Status:** DRAFT → Fable review → TDD → ship
**Fixes:** Gap #2 of the "is he" follow-up bug ([[project_person_area_yesno_bug]]) — the LAST remaining piece.
**Delta-spec** (mirror of the shipped A3 guard, per [[feedback_reuse_prior_designs]]); reuses A3's
`RewriteResult.clarify_text` + the message_handler short-circuit verbatim — NO new plumbing.

## 1. Problem
A bare singular-personal-pronoun follow-up ("is **he** working on ML?") whose antecedent the system
CANNOT resolve is currently answered anyway: the rewrite layer passes the pronoun through unchanged, the
router drops it, and the user gets a de-pronouned (population/generic) answer with no signal the bot
didn't understand who they meant. Owner's standard: **resolve the referent OR clarify — never silently
drop it and answer.**

## 2. The A3 relationship (why this is the twin)
| Case | Preceding context | "he/his" resolves to | Today | Want |
|---|---|---|---|---|
| **A3** (shipped) | assistant turn named ≥2 people | ambiguous (any of N) | clarify ✅ | clarify |
| **1 antecedent** | named/prose 1 person | that person | LLM rewrites ✅ | (unchanged) |
| **Gap #2** (this) | **0 resolvable antecedent** | nobody | answers de-pronouned ❌ | **clarify** |

## 3. Core decision — POST-LLM, not pre-LLM
A3 layer 1 is pre-LLM because it reads TAGGED person_names (structured turns). Gap #2 **cannot** gate on
"0 tagged names": a RAG/prose answer that named exactly one person carries NO tags, yet the LLM (which
sees the prose) resolves the pronoun fine. Gating pre-LLM on 0 tags would wrongly clarify those.

**So: let the LLM try first, and treat a BARE PRONOUN SURVIVING IN THE RESULT as the unresolvable signal.**
The LLM sees prose, so if it resolves the reference a NAME replaces the pronoun.

**⚠️ REVISED from `rewritten==False` → bareness-of-RESULT (live Granite finding, 2026-07-04).** The first
implementation gated on `rewritten==False` (LLM returned the message unchanged). Live testing exposed a
hole: real Granite rewrote "is he working on machine learning" → **"Is he working on machine learning?"** —
a COSMETIC change (capitalization + "?") that leaves the pronoun UNRESOLVED but sets `rewritten==True`, so
the old gate did NOT clarify and the bug survived. **Fix:** fire the clarify when a BARE singular-personal
pronoun survives in the RESOLVED query — `_bare_singular_pronoun(verified)` — independent of `rewritten`.
Rationale: a real resolution puts a NAME (content word) before any residual pronoun → result not bare; a
cosmetic-only rewrite leaves a leading bare pronoun → clarify. This also handles CO-REFERENCE for free:
"is he working with his students" → "Is Koutis working with his students" → residual "his" has "Koutis"
before it → not bare → no clarify. `verify_rewrite` still discards hallucinated/roster picks to passthrough
(verified==message), which are then correctly clarified iff the message is bare (strictly better than
answering an arbitrary pick).

**Safe-degradation principle:** a clarify is never harmful — worst case it asks "who?" on a pronoun the
LLM *could* have resolved but didn't (LLM imperfection / an over-strict verify). That is far better than
the current confident de-pronouned answer, and low-frequency. We accept rare over-clarify to eliminate
silent wrong-answers.

## 4. Scope (conservative, mirrors A3)
- **Singular personal only** — reuse `_SINGULAR_PERSONAL = his|her|hers|him|he|she`. Plural (their/them/
  they) is deferred (A3 excludes it too: plural over a set has a valid antecedent). `its`/`it` excluded.
- Fires ONLY on genuine passthrough of such a pronoun. A successful rewrite (rewritten==True) never clarifies.
- **BARE pronoun only (Fable required-change):** clarify ONLY when NO content word (`_content_words`)
  precedes the FIRST `_SINGULAR_PERSONAL` match — i.e. the pronoun IS the referential subject, not an
  anaphor to an in-message antecedent. Kills the false nag on self-contained compound queries:
  "who is bryan pfister and what's **his** h-index" (prefix has content words bryan/pfister → NOT bare →
  passthrough, today's behavior), "does koutis work with **his** students" (koutis → NOT bare). Lowercase-
  proof (uses content-word set, not capitalization). "is **he** working on ML" (prefix "is" = stopword →
  bare → clarify). Leakage direction is safe: a non-antecedent content prefix ("tell me about his research"
  → "tell" is content) merely degrades to TODAY's passthrough, never a wrong nag.

## 5. Change — `context_rewrite.resolve_query` (+ helpers + a flag) and a 1-line handler reason
Gated by a NEW flag `UNRESOLVED_PRONOUN_CLARIFY_ENABLED` (default OFF; independent rollback from A3).
```python
def _bare_singular_pronoun(message: str) -> bool:
    """True iff a singular-personal pronoun appears with NO content word before it (the pronoun IS the
    referential subject, not an anaphor to an in-message antecedent). Lowercase-proof."""
    m = _SINGULAR_PERSONAL.search(message or "")
    return bool(m) and not _content_words(message[:m.start()])

def _unresolved_clarify(message: str) -> str:
    m = _SINGULAR_PERSONAL.search(message or "")
    pron = m.group(0).lower() if m else "that person"
    return f'I\'m not sure who "{pron}" refers to here — could you tell me the name?'
```
In `resolve_query`, two insertion points, BOTH gated on `clarify_on AND _bare_singular_pronoun(message)`:
1. **No-history / first-message path** (existing early `not history_turns or llm is None` return): also
   require `llm is not None` (llm None = system degraded, NOT ambiguity → stay passthrough).
2. **Post-verify (REVISED):** after computing `verified`, fire iff `_bare_singular_pronoun(verified)` (a
   bare pronoun survives the rewrite → unresolved) AND the LLM returned a non-empty `resolved` (empty =
   system noise → passthrough). Independent of `rewritten` (catches the cosmetic-rewrite hole above). An
   LLM *exception* already returns before this point → no clarify (system error). Echoes the pronoun from
   `verified` (the accurate residual pronoun).

Emit `logger.info("context_rewrite[gap2]: clarify unresolved pronoun (msg=%r)", message)` on fire (measurability).
Ordering: A3 layer 1 (≥2 named) fires FIRST (pre-LLM, returns early), keeping its candidate-listing clarify;
Gap #2 only handles the unresolved passthrough. Never double-fires (A3 requires history; point-1 requires none).

## 6. Handler — distinct abstain_reason (1-line, better analytics)
`RewriteResult` gains `clarify_reason: str = "ambiguous-antecedent"` (A3 keeps the default). Gap #2 sets
`clarify_reason="unresolved-antecedent"`. `message_handler.py:357` uses `abstain_reason=_rr.clarify_reason`
instead of the hardcoded string — so eval/analytics split Gap #2 clarifies from A3's (honest measurement,
[[project_ws4_abstention]] is_abstain tagging). Backward-compatible (A3 unchanged via the default).

## 7. What this does NOT do
- No plural-pronoun clarify (deferred). No change to A3, verify_rewrite, the router, or the LLM prompt.
- Does not attempt to LIST candidates (there are none — it's unresolvable); the message just asks for the name.

## 8. TDD plan (bot/tests/test_gap2_unresolvable_clarify.py)
- **flag ON, unresolvable:** history names NOBODY (e.g. a topic/org turn) + "is he working on ML" → the
  StubLLM returns the message unchanged (simulating "can't resolve") → `resolve_query` returns
  `clarify_text` set, `rewritten==False`. (Assert the pronoun is echoed.)
- **flag ON, no history:** "is he working on ML" as first message, llm present → clarify.
- **flag ON, llm is None:** no clarify (system-degraded passthrough).
- **flag ON, resolvable (single prose antecedent):** StubLLM returns a valid rewrite present in history →
  rewritten==True, `clarify_text is None` (LLM resolved; no over-clarify).
- **flag ON, A3 precedence:** ≥2-name roster + "is he …" → A3's candidate-listing clarify fires (not Gap #2's).
- **flag ON, plural pronoun:** "are they working on ML" unresolvable → NO clarify (out of scope).
- **flag ON, in-message antecedent (Fable req):** "who is Bryan Pfister and what is his h-index" (LLM
  returns unchanged) → NO clarify (not bare); AND the lowercase "does koutis work with his students" → NO clarify.
- **flag ON, empty LLM response:** StubLLM returns "" → passthrough, NO clarify (system noise, not ambiguity).
- **flag ON, distinct reason:** an unresolvable bare-pronoun clarify carries `clarify_reason="unresolved-antecedent"`.
- **flag OFF:** every above → old behavior (passthrough, `clarify_text is None`) — zero change when off.
- **non-pronoun follow-up:** "what about the deadline" unresolvable → NO clarify (not a personal pronoun).

## 9. Goals checklist (per [[feedback_review_against_plan]]) — SHIPPED 2026-07-04 (17 tests, Fable APPROVED)
- [x] unresolvable singular-personal pronoun → clarify (§5, both insertion points; live-verified real Granite)
- [x] resolvable pronoun still resolves, incl. COSMETIC rewrite + co-reference (bareness-of-result gate, §3)
- [x] A3 ≥2-name case keeps its own clarify + reason (precedence, §5; tested)
- [x] flag OFF = literal zero behavior change (§5; diff-verified by Fable)
- [x] plural pronoun / non-pronoun / in-message-antecedent → no clarify (§4, §7; tested lowercase-proof)
- [x] handler change = 1 line (distinct `unresolved-antecedent` reason, §6); router/verify/prompt untouched

**Verification:** 17 gap2 tests + A3 + context_rewrite green (61 total), 0 regressions. Live real-Granite
repro (flag ON): unresolvable→clarify, resolvable(prose)→"Is Ioannis Koutis working on ML" no-clarify,
in-message-antecedent→no-clarify, first-message→clarify. Fable APPROVED design + impl (re-blessed the
live-driven bareness-of-result change). Shipped with UNRESOLVED_PRONOUN_CLARIFY_ENABLED=1.
```

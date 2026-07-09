# Gate-2 precision fix (positive-span reframe) — delta design

> **Delta spec.** Parent designs: `2026-07-02-ws4-abstention-*` (the answer gate) and the Gate-2
> answerability check in `v2/core/retrieval/answer_gate.py` / `faithfulness.py`. This changes ONE
> behavior inside the existing gate; everything else in WS4 stands. Ships behind the existing
> `ANSWER_GATE_ENABLED` flag (already live) — no new flag.

## Review outcome (rev 2 — 2026-07-08)

Both required expert reviews returned **APPROVE-WITH-CHANGES**; they converged on the same issues.
Resolutions folded into this rev:

| # | Must-fix (both reviewers unless noted) | Resolution in this rev |
|---|----------------------------------------|------------------------|
| 1 | Shared-prompt blast radius — `_GATE2_SYSTEM` also drives `_live_relevance_ok` (:818, flag-off) + `eval_gate_shadow.py` | Disclosed in Change surface; recommend one shared prompt (desirable for live too) + live-relevance regression note |
| 2 | Invariant #2 overstated — `robust_grounded` checks the *checker's quote*, not the answer | Corrected; label semantics = sole non-typed guard → Layer-3 hard gate + measure-first coupling check |
| 3 | `fmt="json"` suppresses a free-text CoT step (RAG) | JSON schema specified, `primary_ask` first field |
| 4 | Fixture non-deterministic (live-DB drift); must freeze + drive full gate | Layer-2 rewritten: re-capture → validate vs diagnostic → freeze committed triples → full-gate replay |
| 5 | Layer-3 must be merge-BLOCKING, not "re-run" (biased 47-sample can't bound global false-answer) | Layer-3 elevated to merge gate: false-answer ≤ 15% |
| 6 | Model-calibration caveat (granite just swapped) | Added; integration-marked, pinned, excluded from CI, re-baseline on swap |
| 7 | Silent drops: COMPOSE_REFUSE (6) + 5 non-`not-in-context` abstains | Added to loud-deferred list |
| 8 | #0/#54 "next semester" = ungrounded secondary claim — owner must sign off knowingly | New "Accepted interim behavior" section (the one owner decision) |
| 9 | Re-adjudicate boundary labels #4/#43/#45 (RAG+SE) | Made a required Layer-2 build step |

Nice-to-haves accepted: measure-first answer↔quote coupling (RAG/SE), extra synthetic fabricated case
(fabricated answer + on-topic responsive span), stale-date count of the 38 keeps, update stale
negative-global framing in `answer_gate.py:10-15` / inline comments / `faithfulness.py:228`.

## Problem (measured, not theorized)

The 1000-question live matrix found ~26.6% own-but-missed **debt**. Fable's build-order call put the
**prose ⅔** first because it is the largest, cheapest, live regression. The prose-gate diagnostic
(`eval/processing_debt/prose_gate_diagnostic.py`, gate ON / live OFF, 82 prose-debt questions) traced
*why* retrieved prose is not surfaced:

| bucket | count | share | meaning |
|--------|-------|-------|---------|
| **GATE_ABSTAIN** | 52 | **63%** | compose produced a correct answer; the faithfulness gate killed it |
| NO_RECALL | 21 | 26% | genuinely low relevance — M2 chunking track, not this fix |
| COMPOSE_REFUSE | 6 | 7% | compose declined despite chunks |
| PRECISION | 0 | 0% | no homonym/off-sense answers |
| KEPT | 3 | 4% | gate kept a correct answer |

Inside GATE_ABSTAIN, **47 of 52** carry the reason `gate2:not-in-context`. Hand-labeling all 47
(`eval/processing_debt/out/gate2_fixture_labeled.md`) found **38 keep / 9 abstain — an 81% false-abstain
rate in that bucket.** The gate is the dominant prose-debt cause, and it is mostly wrong.

### Root cause

`decide_after_gate2()` abstains whenever `gate2_label == NOT_IN_CONTEXT`. The label comes from the
`_GATE2_SYSTEM` prompt, which frames a **negative-global** question:

> "Decide whether the CONTEXT contains a specific answer to the QUESTION … A topic merely being
> mentioned is NOT support."

At temp 0 the checker reads "a specific answer to the QUESTION" as "the **complete, exact** answer to
the **whole** question." So it returns NOT_IN_CONTEXT when:

- the question is **compound** and one atom is unowned — `who teaching cs 634 next semester`: the
  instructor IS grounded, but "next semester" scheduling is not, so the whole thing → NOT_IN_CONTEXT
  (this is the same case the owner flagged, and the same one the future query-decomposition track
  targets — see `project_query_decomposition`);
- the composed answer **paraphrases** the source rather than quoting it verbatim;
- the answer is a correct **process/policy** answer but no single sentence reads as "the answer."

The 9 true-abstains are structurally different: the context is about a **different topic** (opt→patent,
less-credits→health-insurance, ML-citations→a non-NJIT person) or is a refusal ("no mention of a Muslim
Student Association"). There, **no grounded span responds to the question's primary ask.**

That structural difference — *is there a grounded span responsive to the primary ask?* — is the signal
the current prompt throws away by asking about the whole answer globally.

## Fix (Fable's positive-span reframe)

Flip Gate-2's criterion from **negative-global** ("is the complete answer present?") to
**positive-span** ("is there a grounded quote responsive to the question's PRIMARY ask?").

- **Answer** iff the checker can copy a verbatim quote from the context that responds to the primary
  information need, and that quote is grounded (survives `robust_grounded`).
- **Abstain** only when **no** such span exists — the context is off-topic, tangential, or a refusal.
- A compound question whose **primary** atom is answered but whose secondary atom is not is
  `PARTIALLY_SUPPORTED` → **answer** (it already routes to answer; today it wrongly gets NOT_IN_CONTEXT).

This is a **prompt + label-semantics** change, not a control-flow rewrite. The decision function keeps
its shape (`SUPPORTED → answer`, else abstain); we change what the LLM is asked so PARTIALLY/​FULLY vs
NOT_IN_CONTEXT tracks primary-ask responsiveness instead of whole-answer completeness.

### What does NOT change (invariants preserved)

1. **Typed-value path** (`assess_pre_gate2` → `answer_has_grounded_type`): money / date / count /
   rate questions still require a grounded value of the expected type. `how much is late payment`
   still needs a grounded dollar figure. Untouched.
2. **Grounding guard** (`robust_grounded`, token-set ≥ 0.7): untouched. **Precise scope (review
   correction):** it verifies *the checker's own supporting quote* appears in the context — it rejects
   a checker that hallucinates a citation. It does **not** re-verify the composed answer against the
   context. So once the label gate loosens, for non-typed prose the **label semantics become the sole
   remaining fabrication guard** (the typed-value path still covers money/date/count/rate). This is the
   real residual exposure — grounded-but-irrelevant paste, which WS4's own docstring says no groundedness
   check catches — and it is why Layer-3 (below) is a HARD merge gate and why a deterministic
   answer↔quote coupling check is evaluated at build (see Change surface).
3. **Parse-fail → abstain** (the `parsed=False` branch): the "capital of France" fabrication-leak fix
   stands. A grounding-checker malfunction still does not answer an out-of-domain question.
4. **Gate-1** (deflect: identity / other-institution / live) and **Granite self-abstain** upstream:
   untouched.
5. Drift is still caught: tangential context yields no primary-responsive span → NOT_IN_CONTEXT →
   abstain. The 9 guardrail cases are the regression proof.

## Change surface (exact)

- `v2/core/retrieval/answer_gate.py`
  - `_GATE2_SYSTEM` (~:122): rewrite to the positive-span framing. Ask the model to (a) restate the
    question's **primary** information need in one clause, (b) copy a verbatim quote that responds to
    THAT, (c) label FULLY_SUPPORTED (primary fully answered) / PARTIALLY_SUPPORTED (primary answered,
    detail or a secondary clause missing) / NOT_IN_CONTEXT (**no** quote responds to the primary ask).
    Keep the "a topic merely mentioned is NOT support" clause — it is what still catches the 9 drifts.
    Keep the literal substrings `"NOT_IN_CONTEXT"` and `"quote"` so the existing
    `test_gate2_prompt_includes_question_and_context` stays green.
  - **JSON schema is load-bearing (review must-fix):** the call runs with `fmt="json"`, which
    grammar-constrains output to the emitted keys — a free-text "restate the primary ask" step would be
    silently dropped. So the restatement MUST be a JSON field, emitted FIRST, to preserve the
    chain-of-thought ordering at temp 0:
    `{"primary_ask": "...", "supporting_quote": "...", "label": "...", "missing_piece": "..."}`.
    `parse_gate2` ignores unknown keys, so adding `primary_ask` is backward-compatible; no parser change.
  - Label set, `parse_gate2`, `Gate2Verdict`, `robust_grounded`, `_SUPPORTED`: **unchanged.** (Note:
    `verify_support`/`quote_grounded` are shadow-only; production grounding is `robust_grounded`.)
- `v2/core/retrieval/faithfulness.py`
  - `decide_after_gate2` (~:221): **logic unchanged.** Its docstring gains a one-line note that
    PARTIALLY_SUPPORTED now means "primary ask answered" so compound questions surface.
  - **Answer↔quote coupling check — MEASURE-FIRST, then decide (review must-fix, no arbitrary cap):**
    when a verdict is SUPPORTED, optionally also require token-set overlap between the checker's grounded
    quote and the **composed answer** (reuse `_norm`), to close the grounded-but-irrelevant-paste channel.
    The threshold is DERIVED from the 38-keep quote↔answer overlap distribution, not a magic constant.
    Build step: measure that distribution first; adopt the check only if it cleanly separates the 38 keeps
    from a fabricated-answer/on-topic-context probe (nice-to-have Gap-3 test). If it does not separate,
    DECLINE the check and record the residual as an accepted-and-measured gap (Layer-3 remains the guard).
- **Shared-prompt blast radius (review must-fix — NOT "no message_handler change"):** `_GATE2_SYSTEM` /
  `gate2_prompt` has THREE consumers, not one:
  - `message_handler.py:882` `_faithfulness_gate` — the KB answer gate (the target). ✅
  - `message_handler.py:818` `_live_relevance_ok` — the A1 live-njit.edu relevance judge, gated by
    `LIVE_RELEVANCE_GATE` (currently **OFF**, not in `.env`; already answer-biased). The reframe
    retargets its criterion too — DORMANT today, inherited when that flag flips.
  - `scripts/eval_gate_shadow.py:66,163` — offline shadow harness.
  **Decision (recommended, pending owner/Fable confirm):** keep ONE shared prompt — the positive-span
  criterion is desirable for the live gate too (same over-abstention logic, and it is already
  answer-biased + off). This is an intentional coupling, not an accident. Add a live-relevance
  regression note to the plan so flipping `LIVE_RELEVANCE_GATE` later re-checks it. (Alternative if
  owner prefers isolation: split into a KB-gate prompt + a live-gate prompt — more surface, deferred
  unless requested.) No code change in message_handler.py either way; no new env flag; no schema change.

## Testing (TDD)

Two layers, because Gate-2 is an LLM call.

**Layer 1 — deterministic unit tests** (`v2/tests/test_answer_gate.py`, extend): the decision
functions are pure. Assert `decide_after_gate2("PARTIALLY_SUPPORTED", <grounded quote>, passages)` →
`("answer", …)`; `NOT_IN_CONTEXT` → abstain; ungrounded quote → `gate2:unsupported`; `parsed=False` →
abstain. These already largely exist; add the PARTIALLY-primary case explicitly. No LLM.

**Layer 2 — LLM regression fixture** (`v2/tests/test_gate2_regression.py`, new;
`@pytest.mark.integration`/slow, **excluded from default CI**, temp 0, model PINNED). Source:
`eval/processing_debt/out/gate2_fixture_labeled.jsonl` (47 cases). Build steps, in order:
  1. **Re-capture FULL passages AND untruncated answers** (the diagnostic stored only `ai[:200]` and no
     passages) by re-running retrieval + compose once for the 47.
  2. **Validate each re-captured case against the diagnostic record** — compare `rank1`/answer-prefix to
     the frozen `prose_gate_diag.jsonl`. The live DB has drifted since the diagnostic (office backfill
     2026-07-05, area expansion, ongoing crawls); any case whose re-retrieval now returns different
     chunks is **re-adjudicated** (its hand-label may no longer describe it) before it counts. Specifically
     **re-adjudicate #4** ("freshman work off campus" — the passage literally continues "*before working
     off-campus…*", so a responsive span may exist → the label, not the fix, may be wrong) **and #45**
     ("scholarship form" vs one Guttenberg process). #43 (I-20 vs I-515A) is the third boundary case.
  3. **Freeze the validated `(question, full_passages, full_answer, expected)` triples into a COMMITTED
     fixture.** The test replays the FULL `_faithfulness_gate` (self-abstain + `assess_pre_gate2` +
     Gate-2) over the frozen snapshot — it does NOT re-run live retrieval per test (otherwise it conflates
     M2 retrieval drift with the gate and is non-reproducible).
  Assertions:
  - **9 MUST-STILL-ABSTAIN** (guardrails, after re-adjudication) → assert abstain. Hard: **9/9.**
    Includes health-insurance drift (#5), opt→patent (#20), ML-citations→wrong-person (#31, note: this
    is really a mis-routed metric query — orthogonal router track, kept only as a gate guardrail), and
    the two "no Muslim Student Association" refusals (#16/#79). *Statistical caveat:* 9/9 bounds the
    guardrail flip-rate at only ~28% (95% conf) — it is a floor, not a proof; Layer-3 is the real guard.
  - **38 MUST-KEEP** → assert answer. Target **≥ 34/38 (~90%)**; the 4-case slack absorbs stale-calendar
    answers (#33/#36/#77 cite 2021–2023). **Build step: count how many keeps are stale — if > 4, revisit
    the ≥34 target BEFORE build.**
  - **≥2 synthetic MUST-ABSTAIN cases:** (a) fabricated answer + off-topic context (trivial); (b) the
    dangerous one — fabricated answer + **on-topic context containing a real responsive span** (locks the
    answer↔quote coupling decision), plus a paraphrased-but-absent quote to lock `robust_grounded`.

**Layer 3 — HARD MERGE GATE, not "re-run and confirm" (review must-fix):** the 47-case fixture is a
biased sample (only 9 negatives, all from the GATE_ABSTAIN bucket) and CANNOT bound global false-answer;
the reframe's failure mode (grounded-but-irrelevant paste) is invisible to it. So the WS4 both-directions
harness (false-answer / false-abstain on the broader set) is a **merge-blocking** checkbox: **false-answer
MUST NOT exceed 15%.** WS4 baseline: false-answer 40%→15%, false-abstain 24%→20%. Success = false-abstain
drops with false-answer held ≤ 15%. Nice-to-have held-out check: re-run the full 82-question prose-gate
diagnostic post-fix.

**Model-calibration caveat:** all thresholds (34/38, 9/9, ≤15%) are calibrated to the CURRENT gen/verify
model (`granite4:tiny-h`, swapped days ago in the VRAM diet) at temp 0. Per the LLM-agnostic hard line
the *prompt* must be model-robust, but the *numbers* are model-specific — re-baseline the fixture on any
LLM swap. Greedy temp-0 decoding is stable but not bit-reproducible across model/GPU/ollama versions →
run this as a pre-merge + on-model-swap gate, not in default CI.

## Expected impact

If ~34–38 of the 47 recover and generalize at the diagnostic's 63% GATE_ABSTAIN share, this recovers
the **largest single slice of prose debt** — Fable's estimate ~40–45 of the ~82 prose-debt questions.
It does **not** touch NO_RECALL (26%, → M2 chunking) or the structured ⅓ (→ query-correction-salvage).
Those remain separate tracks.

## Goals checklist (fill at PR)

- [ ] `_GATE2_SYSTEM` reframed to positive-span; drift clause + literal `NOT_IN_CONTEXT`/`quote` retained.
- [ ] JSON schema `{primary_ask, supporting_quote, label, missing_piece}` — `primary_ask` first.
- [ ] Shared-prompt coupling documented; live-relevance (`_live_relevance_ok`) regression note added.
- [ ] Answer↔quote coupling: distribution measured; check adopted OR declined-and-measured.
- [ ] Compose time/schedule-qualifier guard clause added to `compose_from_rows` (+ RAG compose path);
      #0/#54 output logged post-build to confirm the qualifier is scoped, not echoed.
- [ ] Typed-value path unchanged (verified by existing tests still green).
- [ ] `robust_grounded` + parse-fail-abstain unchanged (verified); invariant #2 wording corrected.
- [ ] Layer-1 unit tests (PARTIALLY-primary answers; ungrounded/parse-fail abstain).
- [ ] Layer-2 frozen committed fixture, full-gate replay: 9/9 guardrail (re-adjudicated #4/#43/#45) +
      ≥34/38 keep (stale count checked) + ≥2 synthetic abstain; integration-marked, model-pinned.
- [ ] Layer-3 (MERGE-BLOCKING) WS4 both-directions eval: false-answer ≤ 15%, false-abstain down.
- [ ] Model-calibration caveat recorded (re-baseline on LLM swap).
- [ ] DEFERRED (loudly): NO_RECALL 26% (M2 chunking); COMPOSE_REFUSE 7% (compose prompt/prefit);
      the 5 non-`not-in-context` GATE_ABSTAINs (3× gate2:unsupported, 1× typed-ungrounded:money,
      1× self-abstain); structured ⅓ (query-correction); compound per-atom answering
      (query-decomposition — covers the #0/#54 "next semester" interim, see Accepted interim below).

## Compound partials — DECIDED: serve-and-scope (Fable, 2026-07-08, owner-delegated)

The reframe surfaces compound questions whose PRIMARY atom is grounded but a SECONDARY clause is not —
flagship #0/#54 `who teaching cs 634 next semester`: instructor grounded; "next semester" scheduling
not. **Decision (Fable, standing in for owner approval): Option A-with-mitigation** — serve the
grounded primary ask, but add a compose guard so the ungrounded time/schedule qualifier is NOT asserted.

Rationale: the two hard lines don't collide here — never-withhold says serve the grounded instructor
(abstaining on it IS the debt we exist to fix); honest-partial forbids only the *"next semester"*
assertion. So serve-and-scope, not abstain. Fable read `compose_from_rows`
(`bot/services/ollama_client.py:~414-441`): its anti-fabrication clauses cover names/attributes/
abbreviations/pronouns but NOTHING forbids echoing a time qualifier from the question, so at temp 0.0 a
"For next semester, CS 634 is taught by X" framing is a live risk — relying on compose to silently drop
it is hoping, not grounding. Option B (hold compounds abstaining) would need the gate to distinguish
"compound with ungrounded atom" from "primary answered" — that is the query-decomposition machinery,
deferred; half-building it in the gate is worse.

**Mitigation (in THIS build):** add one clause to the `compose_from_rows` system prompt (and the RAG
compose prompt if the gated path composes elsewhere): *"If the user's question contains a time or
schedule qualifier (e.g. 'next semester', 'this fall') that the Facts do not confirm, do NOT assert it —
answer what the Facts state and note that per-semester scheduling isn't in our data."* Prompt-only, no
new machinery; true per-atom answering stays with the query-decomposition track. Post-build, log the
#0/#54 fixture output to confirm the qualifier is scoped, not echoed.

## Review dispatch

Per the EXPERT-REVIEW HARD GATE: this is a retrieval/answer change → needs a RAG/LLM-researcher
review **and** a senior-eng diff pass, plus owner sign-off, before build. Fable already set the
build order and endorsed the positive-span direction; this spec is the concrete artifact for the
formal review. Reviewers must check: (a) the reframe cannot raise false-answer (the never-withhold vs
never-fabricate balance), (b) the 9 guardrails are genuinely structurally distinct from the 38, (c)
goals-vs-plan completeness.

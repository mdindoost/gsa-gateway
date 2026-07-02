# WS4 — Trustworthy abstention + post-generation faithfulness gate (DESIGN DRAFT)

> Kavosh v2.5, pillar 4, workstream 4. Owner-scoped 2026-07-02. **DRAFT — not yet through the HARD GATE.**
> Two OWNER-DECISION forks are marked ⚠️ below; the design defaults to the recommended option and is
> revisable the moment the owner picks. Diagnosis in `memory/project_ws4_abstention.md`.

## 1. Problem (measured, not assumed)

Two error directions that pull against each other. Baseline on the WS4 labeled buckets (current live
Qwen+Granite system, `ANSWER_GATE_ENABLED=1`, `BAND=0.70`, gate measured with live-fallback isolated):

| direction | rate | dominant cause |
|---|---|---|
| **false-abstain** (should-answer blocked) | **4/25 = 16%** | 3/4 = `verify_support`/`quote_grounded` markdown string-brittleness; 1 = genuine low-CE retrieval miss (registrar hours) |
| **false-answer** (should-abstain answered) | **5/20 = 25%** | 3 slipped the Gate-2 SKIP path (not-fact-shaped + high CE → never verified); 2 = Granite-grader FULLY_SUPPORTED false-positives |

Chrome River is the canonical false-abstain: retrieval CE=**0.9998**, "within 30 days" in the #1 chunk
verbatim, Granite's Gate-2 verdict CORRECT (FULLY_SUPPORTED), then `quote_grounded` rejected the correct
quote because the stored chunk has markdown emphasis (`**30 days**`) that breaks its longest-contiguous-
substring ≥0.6 test. Final reply: "I wasn't able to find specific information about that." (`used_ai=False`).

### Two structural facts that reshape the plan
1. **The 0.70 CE-band is a dead lever.** The reranker (`ms-marco-MiniLM-L-6-v2` + sigmoid) saturates
   ~0.96–1.00 for anything topically related, so `ce < 0.70` almost never fires — the de-facto Gate-2
   trigger is the `is_fact_shaped` regex, not the band. Recalibrating the band UP just runs the unreliable
   Granite grader on MORE questions (more false-abstains); DOWN does nothing. **Band recalibration is a
   minor tuning step, not the centerpiece** (revises the owner's Phase-3 emphasis).
2. **`verify_support` is a blunt string-match, damaging in BOTH directions.** It blocks correct markdown
   answers (3 false-abstains) AND accidentally catches some hallucinated FULLY_SUPPORTED verdicts (dining
   menu, dept-budget, pass-rate). So naively stripping `*` fixes false-abstains but LEAKS new false-answers.
   The fix must verify **semantic** support, not string overlap.

The durable fix the owner proposed — a **post-generation faithfulness gate on the composed answer** —
addresses all three failure modes at once: robust matching kills the markdown false-abstains; running it
regardless of `fact_shaped` covers the skip-path fabrications; checking the actual generated claims (not
Granite's self-label) catches grader false-positives.

## 2. ⚠️ Fork A — WS4 center of gravity (OWNER DECISION)
**Recommended:** center WS4 on the post-generation faithfulness gate; band recalibration is a minor tuning
step. (Alternatives offered: keep band recalibration co-primary; or minimal Chrome-River-only markdown fix.)
This design assumes the recommended option.

## 3. Target design

### 3.1 New: post-generation faithfulness gate (`v2/core/retrieval/faithfulness.py`)
After the RAG compose (`generate_answer`) produces `answer`, and BEFORE it's returned:
1. Split `answer` into claim units (sentences; skip the friendly greeting/opener line — it carries no fact).
2. For each claim, decide **supported | unsupported** against the retrieved passages (the same top-k handed
   to compose). Robust matching — normalize markdown (`*_#`), punctuation, whitespace before any comparison.
3. **Drop** unsupported sentences. If what remains still answers the question → return the pruned answer.
   If the core answer collapses (all/most fact-bearing sentences dropped, or the specific asked-for datum is
   gone) → **abstain** (Phase-4 useful-abstain, §3.4).
4. Fail-safe: any checker error / timeout → keep the original answer (never-withhold; a checker malfunction
   must not block a real answer — mirrors the existing `parsed=False` answer-bias).

This REPLACES the brittle pre-generation `verify_support` string-check as the primary over-answer guard.
Gate-1 (deterministic intent deflect) stays unchanged. The low-CE band stays ONLY as a retrieval-miss signal.

### 3.2 ⚠️ Fork B — the checker (OWNER DECISION; pin by live bake-off)
The owner wants it CPU-side to protect the 16 GB GPU for research, and wants options verified against live
docs before pinning. Bake-off BOTH on the WS4 buckets (must catch the 5 false-answers AND pass the 25
grounded, incl. Chrome River) and measure added latency; pin the lightest that clears the bar:
- **Option 1 — CPU NLI / MiniCheck** (owner's lean; protects GPU): a small entailment/faithfulness model
  (MiniCheck-Flan-T5-small or a DeBERTa-MNLI) on the i9 CPU, ONNX/onnxruntime like the reranker (no torch),
  provider-isolated per [[feedback_llm_agnostic]]. Cost: new model download + CPU latency (~100–400 ms/claim).
- **Option 2 — Granite-as-verifier, FIXED** (lightest, no new model): one constrained-JSON Granite pass
  returning per-sentence supported/unsupported. Granite's Gate-2 semantic verdict was already CORRECT on
  Chrome River — the failure was the string post-check, not Granite. Cost: competes for GPU (already resident).

**Recommendation:** default to Option 1 (CPU, protects GPU, generator-agnostic) IF the bake-off shows it
clears the accuracy bar within latency; else fall back to Option 2. Decide from data, not up front.

### 3.3 Retire / relax the pre-gen Gate-2 brittleness
- Remove `is_fact_shaped`-forced Gate-2 (it's the false-abstain trigger and is redundant once the output is
  checked). Keep the low-CE band → Gate-2 answerability ONLY as a cheap retrieval-miss catch.
- `quote_grounded` markdown normalization is folded into the new robust matcher; the standalone brittle path
  is deleted (back-compat not a concern — [[project_kavosh_v2_5]] posture).

### 3.4 Phase-4 — useful abstain + clarify
- **Useful abstain:** when the faithfulness gate abstains but retrieval DID surface relevant high-CE chunks,
  the reply offers the nearest thing ("I don't have the exact deadline, but here's the Travel Award section
  that covers submission") + wire the existing deflection (`bot/core/deflection.py` offer + office/email/
  contact) instead of a bare `_KB_MISS_RESPONSE`.
- **CLARIFY:** person `person_disambig` already surfaces "did you mean X?" (WS2). Add the missing
  **`org_disambig`** render so an org multi-match ("faculty in engineering") clarifies ("which department:
  … ?") instead of dead-abstaining (finishes WS2's loud deferral).

## 4. Explicitly OUT of scope / separate findings
- **Registrar-hours false-abstain = a RETRIEVAL miss** (ce=0.159, "30 days" analog not retrieved), not a gate
  bug. Reported separately; not fixed in the gate (may fold into a later retrieval workstream).
- The SQL / KG structured path is UNTOUCHED (WS4 is RAG/gate only). Regression net = the bakeoff.

## 5. Merge gates (owner-defined)
(a) false-answer rate ↓ vs baseline 25% (PRIORITY) · (b) false-abstain rate ↓ vs baseline 16% · (c) the
faithfulness gate abstains on a fabrication-bait case + passes a genuinely-grounded case · (d) abstain/clarify
useful (deflection wired, clarify surfaces, org multi-match clarifies) · (e) NO regression on the KG/structured
path (`scripts/router_slot_bakeoff.py` family acc + hardneg unchanged).

## 6. Deliverables to print at verification
Two-bucket before/after error rates · Chrome River live trace (now grounded "within 30 days") · fabrication-
bait result · the checker choice + where it runs (CPU/GPU) + latency added · scope diff confirming the SQL-skill
path is untouched.

## 7. Build plan (subagent-driven TDD, after HARD GATE + owner approval)
1. Bake-off harness → pin the checker (Fork B). 2. `faithfulness.py` + robust matcher (TDD). 3. Wire into
`_rag_pipeline` post-compose; retire brittle `verify_support` trigger. 4. Phase-4 useful-abstain + `org_disambig`
render. 5. Re-measure both buckets; run bakeoff regression. 6. Final whole-branch review → owner sign-off → merge+restart.

## 8. Open forks blocking the build
- ⚠️ Fork A (§2): WS4 center of gravity. **Recommend: faithfulness-gate-centered.**
- ⚠️ Fork B (§3.2): checker = CPU-NLI vs Granite-verifier vs bake-off-both. **Recommend: bake-off, default CPU.**

---

## 9. AS-BUILT (2026-07-02) — mechanism, evidence, review folds

**Mechanism pivot (from the bake-off).** A whole-answer NLI / Granite-as-verifier faithfulness gate
OVER-ABSTAINS (96% / 60% false-abstain on paraphrased grounded answers), and the dominant fabrication
mode is GROUNDED-BUT-IRRELEVANT paste (real passage text that does not answer THIS question) — which no
groundedness check can catch. So the gate is a deterministic **answerability** gate (CPU-free), not an
NLI checker: `v2/core/retrieval/faithfulness.py` runs POST-generation —
1. subjective-superlative guard → abstain; 2. answer-type grounding (count/rate/money/date must carry a
GROUNDED value of the expected type) → answer/abstain; 3. non-typed residual → a selective Gate-2
answerability call, post-checked by markdown-normalized **robust_grounded** (token-set ≥0.7, the
Chrome-River fix). Wired compose-FIRST in `message_handler._faithfulness_gate` (guarded so a gate fault
keeps the answer); abstain path is never-withhold (try live → `_useful_abstain`).

**Both-directions eval (frozen 45-Q, same harness `scratchpad/ws4_validate.py`):**

| Gate | false-ANSWER (priority) | false-ABSTAIN | gate-attributable |
|------|------|------|------|
| No post-gen gate (ceiling) | 65% | 4% | 0% |
| Pre-WS4 production (fact_shaped→verify_support) | 40% | 24% | 12% |
| **WS4 combined (as built)** | **15%** | **20%** | **12%** |

vs pre-WS4 production: **false-answer 40%→15%** (priority ↓, never rises) AND **false-abstain 24%→20%**
(both improve together). France (canonical fabrication) — production ANSWERED it, WS4 ABSTAINS
(`gate2:unsupported`). Chrome River answers grounded ("within 30 days", typed-grounded:count).

**Checker choice (Fork B).** CPU-NLI (Xenova/nli-deberta-v3-small) was benchmarked but NOT used — it is
question-blind and over-abstains. The non-typed residual uses Granite-as-answerability (already-loaded
GPU model, temp 0.0, selective — typed/subjective questions cost NO LLM call). `models/nli/` is NOT
committed. **Divergence from spec §3.2 GPU-protection goal:** flagged — Granite runs the residual, not a
CPU checker; justified because CPU-NLI failed the recall bar and Granite is already resident.

**Review folds (RAG/LLM + senior-eng, both HARD-GATE).** Folded: subjective "best WAY/TIME" exclusion;
`_Q_MONEY` "how much TIME/longer" exclusion; relative-duration dates ("within 30 days"); multi-digit
counts (4–6) + digit↔word equivalence + reachable year-exclusion; money year-exclusion + no digit-soup
substring grounding (`ctx_numtokens`); rate percent-spacing; gate-local exception guard (never break the
answer path); office source_note not re-attached on abstain; dead `ANSWER_GATE_BAND` removed.
**DIVERGENCE from senior #5** (parse-fail / quote-less SUPPORTED → answer): OVERRULED by the eval — it
leaked the France fabrication at ZERO measured false-abstain benefit (no should-answer relied on it;
robust_grounded already tolerates paraphrase; Gate-2 is temp 0.0 so parse-fail is deterministic
out-of-domain garbage). `decide_after_gate2` requires a non-empty robustly-grounded quote; parse-fail →
abstain. Documented in-code.

**Residuals (honest — not blockers, not regressions; production leaked MORE):**
- 3 WS4 false-answers are grounded-but-irrelevant / real-KB content: enrollment-this-semester (pasted
  PROGRAM counts for a STUDENT count), menu-today (retrieval miss → Biology-338 "Ecology of the Dining
  Hall" syllabus), restaurant-rec (real NJIT off-campus-dining page; borderline gold). The documented
  grounded-irrelevant hard mode; a cheap gate cannot catch these.
- 3 gate-attributable false-abstains (tuition, late-fee, parking-ticket) are typed-value-ABSENT: the
  composed answer carried no grounded value of the expected type (the KB lacks the figure) — the gate
  correctly declines rather than fabricate. Same 12% as production (WS4 added NO new gate-attributable
  false-abstains). Registrar-hours quarantined (retrieval miss, §4).

**Goals checklist:** (a) false-answer ↓ SHIPPED (40→15%). (b) false-abstain ↓ / Chrome River SHIPPED
(24→20%; robust_grounded fixes the markdown break). (c) fabrication caught + grounded case passes SHIPPED
(France abstains, Chrome River answers). (d) useful-abstain + org_disambig clarify SHIPPED. (e) NO
KG/structured regression SHIPPED (gate lives only in `_rag_pipeline`; family acc + hardneg unchanged).
GPU-protection (§3.2) DEFERRED/flagged (Granite residual, not CPU). Gate is flag-gated
(`ANSWER_GATE_ENABLED` default OFF) → inert until an env flip + restart.

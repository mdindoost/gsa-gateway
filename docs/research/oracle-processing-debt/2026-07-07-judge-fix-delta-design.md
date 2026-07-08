# Delta-Design — Entailment-Judge Fix (Processing-Debt instrument)

**Date:** 2026-07-07  ·  **Branch:** `feat/processing-debt-pilot` (unmerged)  ·  **Scope:** fix the
Set-A validation FAIL (73.8% debt inflated ~2×). Read `2026-07-07-setA-validation-findings.md` first.

## Problem (proven, not hypothesized)
The presence-check entailment judge hardcodes `granite4:tiny-h`, which returns **"unsure" for
almost every fact–span pair, including obviously unrelated ones**. Combined with the R4
`unsure→present` lean (present iff any span is "yes" OR "unsure"), unrelated KB spans mark a fact
as OWNED. Result: 76% of owned-misses rest only on "unsure"; debt inflated ~2×.

### Bake-off evidence (11 real Set-A (fact,span) pairs, human-adjudicated; `scratchpad/judge_bakeoff.py` + `nli_bakeoff.py`)
| Judge | unrelated correctly rejected | false-"present" 🐛 | genuine kept | ms/pair | calibrated |
|---|---|---|---|---|---|
| granite4:tiny-h *(current)* | 1/7 | **6/7** | 4/4 | ~500 | no |
| llama3.1:8b | 7/7 | 0 | 4/4 | ~500* | no |
| gemma3:12b | 7/7 | 0 | 4/4 | 700–1300 | no |
| **NLI deberta-v3** | 7/7 | 0 | 4/4 | **33** | **yes** |

NLI probabilities cleanly separate: unrelated → entail≈0.00 / neutral≈1.00; genuine → entail 0.86–1.00.
Nothing landed in the ambiguous band. *(NLI subtlety: an unrelated span is NEUTRAL not contradiction,
so the mapping must fold neutral→NOT-present — that is what fixes the bug.)*

## The fix (3 changes, all on the pilot package `eval/processing_debt/`)

### 1. Judge model = configurable, NLI-primary + gemma escalation (new `nli_judge.py`)
- New module `nli_judge.py` mirrors `v2/core/retrieval/reranker.py` EXACTLY (onnxruntime + tokenizers,
  no torch; auto-download `Xenova/nli-deberta-v3-base` → `models/nli/`; CPU provider; batched;
  fail-safe → returns None so caller can fall back). `score(fact, span)` and `score_batch(fact, spans)`
  return `P(entail)` (softmax index 1; config id2label 0=contra,1=entail,2=neutral).
- `entailment.py` gains `entail_score(fact, text) -> float` and keeps `entail_verdict(...) -> yes|no|unsure`,
  now derived from the score: `yes` if `P≥HI` (0.5), `unsure` if `LO≤P<HI` (0.35), else `no`.
  **Model is env-selected** (`PD_JUDGE=nli|gemma|llama|granite`, default `nli`) — LLM-agnostic HARD rule.
  `entails(...)` (IN_ANSWER bool) stays `== "yes"`.
- **Escalation:** when `PD_JUDGE=nli` and a score is in the borderline band `[LO,HI)`, re-judge that ONE
  pair with `gemma3:12b` (generative yes/no) and take its verdict. Band was ~empty in the test → rare,
  cheap. Escalation model env-configurable (`PD_ESCALATE=gemma3:12b`, or `off`).

### 2. Presence lean = require confident entailment (`presence_check.py`)
- Replace "present iff yes OR unsure" with **present iff verdict == "yes"** (i.e. `P(entail) ≥ HI`).
  This settles the R4/Q2 lean: the headline counts only confident ownership.
- Keep a reported **low-confidence band**: spans in `[LO,HI)` recorded in `PresenceResult` (new/kept
  `unsure_only`/`low_conf` field) but NOT counted as present in the headline debt. Surfaces in the report
  as a separate bucket, not silently dropped.
- **Batching:** presence has up to ~130 candidate spans/fact. Add `score_batch(fact, [spans])` so ALL
  spans for a fact go through NLI in ONE inference call → the ~4hr Set-A run collapses to minutes.
  (LLM backends keep the per-span loop; NLI uses the batch path.)

### 3. classify.py — interface unchanged
`deps.entails` (IN_ANSWER) and `deps.presence` signatures unchanged; they pick up the new judge
through the same imports. No orchestration change.

## Non-goals (explicitly deferred, NOT silently dropped)
- Self-contained nuggets (pronoun resolution) — findings fix #3. Separate pass.
- Topicality/materiality gate on real-log sampling — findings fix #4. Separate pass.
- These two remain open; they contribute to the residual noise but the JUDGE+LEAN is the dominant lever.

## Verification plan
- TDD: `nli_judge` (mapping, batch, fail-safe), `entailment` (env-select, band thresholds, escalation
  trigger), `presence` (confident-only present + low-conf band populated). Reuse `judge_bakeoff.py`/
  `nli_bakeoff.py` as a regression gold set (11 pairs, must stay 7/7 + 4/4).
- Then **re-run Set A on the CACHED oracle answers** (`.cache/oracle/`, `resume=True`) → ~zero new Brave
  spend → 100% human adjudication → **Cohen's κ (SC1 ≥0.6 go/no-go)**.

## Risk / caveats for reviewers to probe
- Gold set is 11 pairs and tests the ENTITY-MISMATCH failure mode (the actual bug). It does NOT
  discriminate NLI vs LLMs on hard SAME-SUBJECT-PARTIAL entailment — is the escalation band the right
  safety net, or do we need a harder validation slice before trusting the number?
- Threshold HI=0.5/LO=0.35 chosen from the (wide-margin) test. Is a threshold sweep on adjudicated
  data warranted, or is the margin large enough that it doesn't matter?
- DeBERTa-v3 512-token truncation vs long DB-window spans: does truncating the span head lose the
  entailing sentence? (reranker already truncates at 512; same risk profile.)
- Does confident-only presence now UNDER-report (false NOT_OWNED) for genuinely-owned facts whose only
  evidence is a paraphrase NLI scores mid-band? The low-conf bucket is meant to catch these for adjudication.

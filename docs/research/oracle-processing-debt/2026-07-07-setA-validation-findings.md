# Set A Validation Findings — the instrument is not yet trustworthy (2026-07-07)

**Bottom line:** the pilot ran end-to-end on 50 real student questions and produced a headline **"73.8%
processing debt" — but that number is inflated ~2× by a measurement artifact, so it must NOT be trusted or
scaled to Sets B/C until fixed.** This is the designed self-validation working: the pilot's first job is to
prove whether it can be believed, and it correctly revealed it cannot — cheaply, before we spent on more.

## What ran
- Set A: 50 real-log questions (16 hi-conf + 14 lo-conf + 12 deflected + 5 positive-control + 3 oracle-blind),
  NJIT-scoped oracle queries, ~4.2 hr local compute, resumable. Cost: ~$2.85 Brave (50 cached calls).
- Result (machine, pre-adjudication): 339 facts, 222 vital → 43 IN_ANSWER, **121 OWNED_NOT_SURFACED**, 58
  DROPPED_ORACLE. Machine debt = 121/164 = **73.8%** (95% CI 61–85%, cluster-bootstrap over 37 questions).
  Stage split: **POOL 114 (94%)**, COMPOSE 5, RANK 2, ROUTER 0.
- Report artifact: `out/pilot_report_A.md`. Adjudication CSV: `out/adjudicate_A.csv`. Facts: `out/facts_A.jsonl`.

## Why the 73.8% is NOT trustworthy — three compounding artifacts

### 1. The entailment judge (granite4:tiny-h) is too weak — it hedges "unsure" for almost everything
Re-judging the top stored evidence span of 25 sampled owned-misses: **5 "yes", 19 "unsure", 1 "no"**.
The judge returns "unsure" even for an OBVIOUSLY unrelated pair ("He joined the program in September 2022"
vs a National University of Singapore professor bio). One vague fact had **107 "confirmed" evidence spans**.

### 2. The "unsure → present" lean converts that hedging into false ownership
The presence check (R4 lean, chosen by the senior review to "never under-report") counts a fact as OWNED if
ANY candidate span is "yes" OR "unsure". Combined with a judge that says "unsure" to most pairs, this marks
facts "owned" against unrelated KB spans. **~76% of the owned-misses rest ONLY on "unsure" verdicts**, not
confident matches. Requiring a confident "yes" would collapse the debt to roughly ~35–40% (rough — needs
proper re-run), still POOL-dominated and still real, but far below 74%.

### 3. Real-log noise: off-topic/meta questions + context-dependent nuggets
- ~35 owned-misses come from off-topic/meta questions real students ask ("who do I contact about this?",
  "تو کی هستی / who are you", "where can i eat cheap", "how to DM you"). The oracle answers them with
  generic NJIT facts the presence check then matches. Heuristic-removing these: 74% → ~66%.
- 16/121 nuggets open with a dangling pronoun ("He…", "His…", "The program…") that cannot be judged in
  isolation — a nugget-quality problem (facts should be self-contained/atomic).

## What IS real (don't overcorrect to zero)
The controls and the confident-"yes" subset show **genuine processing debt exists** and is **POOL-dominated**:
real owned facts that never enter the retrieval candidate pool (Shantanu Sharma's joint Data-Science
appointment, MS-CS admission requirements, Vincent Oria's awards — all multi-probe, confident matches). The
*direction* of the thesis holds; the *magnitude* is what's untrustworthy.

## SC1 verdict (the go/no-go): FAIL — do not scale
Formal κ wasn't computed, but the presence judgments disagree with careful reality often enough that κ would
be well below 0.6. Per the plan (R12): **stop after Set A, fix the instrument, do NOT spend on Sets B/C.**

## What to fix before re-running (in priority order)
1. **Stronger entailment judge.** Swap granite4:tiny-h for a capable model (the main llama3.1:8b / Granite
   generation model, or a dedicated NLI model) for presence + IN_ANSWER entailment. This is the biggest lever
   — the whole instrument is only as good as this judge. (LLM-agnostic: config swap + re-run.)
2. **Reconsider the "unsure → present" lean.** It was chosen to never under-report but massively over-reports
   here. Options: require "yes" for presence; or keep "unsure" only when paired with strong lexical overlap;
   or treat unsure-only presence as a separate low-confidence bucket excluded from the headline.
3. **Self-contained nuggets.** The nuggetizer should resolve pronouns / emit atomic standalone claims
   (AutoNuggetizer/RAGChecker protocol) so facts are judgeable in isolation.
4. **Materiality / question-scope filter.** Off-topic and meta-about-the-bot questions shouldn't enter the
   owned-debt denominator (or should be a separate stratum). Real-log sampling needs a topicality gate.

## Cost / value of the pilot so far
~$3.5 Brave + ~5 hr compute. It produced a working, resumable instrument AND, more importantly, proved the
naive number is 2× inflated and localized exactly why — so we fix the judge instead of shipping a wrong 74%.
That is the pilot succeeding, not failing.

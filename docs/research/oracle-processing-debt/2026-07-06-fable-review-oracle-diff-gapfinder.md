# Fable review — Brave-oracle "processing-debt" gap-finder

*Date: 2026-07-06 · Reviewer: Fable (senior RAG/LLM research reviewer) · Status: input to design*

> Context for this review lives in the sibling deep-research report (same folder) and the eventual
> design spec in `docs/superpowers/specs/`. This file preserves Fable's raw opinion so we don't lose it.

## The project under review
Use **Brave Answers** (web-grounded LLM, answer + citations, ~$0.057/query, $30 budget) as a cheap
**diagnostic oracle** (NOT a knowledge source). For every fact in the oracle's answer that OUR answer
missed, do a second lookup: is that fact present in OUR corpus?
- Present but not surfaced → **processing gap** (routing/retrieval/rerank/compose) — the fixable gold.
- Absent → knowledge/crawl gap (separate track).
Headline metric = "% of facts we already own that we failed to surface" = **processing debt**, sliced by
pipeline stage. Pilot ~40–60 questions first (~$3–4), then decide whether to scale.

Owner thesis: *we are not missing knowledge (near-complete authoritative corpus); we are missing
processing. So this is process-distillation, not knowledge-distillation.*

## Fable's verdict
**Proceed — but fix the design first; run the literature pass in parallel, not as a blocker.**
The thesis is sound AND falsifiable by this method (step 3b is what turns the assertion into a
measurement). Four required fixes before spending the first $3.

## Three flaws that would silently corrupt results
1. **Circular corpus check (bias in the flattering direction).** "Run the missed fact as a query
   against KB/KG" uses the retrieval-system-under-test to generate ground truth about itself →
   systematically **understates** processing debt. **Fix:** presence check must be an *exhaustive,
   non-production* search — direct SQL over the KG, FTS over ALL `knowledge_items` *including excluded
   types* (`publication` is excluded from the answer corpus — a fact living only there is a
   processing/config decision, not a knowledge gap), brute-force embedding scan with generous k, plain
   `grep` of source text at pilot scale. Presence = "exists anywhere in our data." Retrievability-by-the-
   pipeline is then one of the *stages you attribute failures to*, not the presence test.
2. **Verbosity asymmetry inflates miss rate.** Brave writes verbose (8–15 atomic facts, much filler);
   our bot is deliberately concise. Naive scoring punishes correct-but-terse answers. **Fix:**
   materiality filter — only score facts *responsive to the question* (TREC "vital vs okay" nugget
   distinction).
3. **Judge-error compounding.** 3 chained LLM judgments per fact (decomposition, in-our-answer,
   in-our-corpus); at 10% error each, much of the "gap list" is noise. **Fix:** at 40–60 questions,
   **human-adjudicate 100%** of pilot judgments and report agreement (kappa). *The pilot's primary
   deliverable is validation of the measurement instrument, not the debt number.* If judge–human
   agreement < ~0.8, scaled numbers are worthless — and you know before spending.

## Other risks it raised
- **Oracle correctness is worse than assumed.** Liu et al. (arXiv:2304.09848): only ~51.5% of
  generative-search-engine sentences fully supported by their citations → expect to drop/flag a large
  minority of oracle facts (roughly halves yield per dollar). Budget for it.
- **Oracle is structurally blind to GSA-internal data** (Wix site, dashboard-sourced officers) → this
  eval underdetects processing bugs in exactly the GSA-internal slice. The corpus-driven auto-eval
  harness must complement it.
- **Metric is demand-weighted, not corpus-weighted.** "% owned facts missed" over 2000 real questions =
  processing debt *on questions people actually ask* (right business metric) — distinct from corpus
  recall (the sampler harness). Report which is which; use both.
- **The 1000 web-needing questions can't produce a processing-debt number** (facts not owned by
  construction) → they feed the knowledge-gap track + a separate "our live-fallback vs Brave" compare,
  and burn our own Brave *Search* free quota. Weight pilot toward the DB-answerable stratum.
- **Cost reality:** $0.057 × 2000 = ~$114 > $30 budget. Realistic ceiling = pilot + ~450–500 questions.
- **Question-set provenance:** the DB-vs-web label is itself part of the hypothesis; let the presence
  check reclassify and report label-error rate. Dedupe/cluster before sampling (logs are head-heavy).

## Pilot design (Fable's sharpening)
- **Stratify by pipeline path**, not topic: structured-router-hit / RAG-answered / live-fallback /
  abstained-or-clarified.
- **Controls:** ~8–10 positive controls (we KNOW our answer is complete — misses there ⇒ decomposition/
  materiality filter is broken); ~5 GSA-internal (oracle SHOULD fail — guard calibration).
- **Adjudicate everything by hand** (feasible at ~50). **Pre-register success criteria:** judge–human
  kappa ≥ 0.8; ≥1 confirmed stage-attributed actionable gap per ~$0.50; stage attribution unambiguous
  for ≥70% of confirmed gaps.
- **Measure the oracle itself:** wrong/stale-answer rate on NJIT domain (if >20%, economics change).
- Split ~40 DB-answerable / ~15 web-needing.

## Literature Fable added (beyond my four: Barnett 2401.05856, FActScore 2305.14251, RAGAS, PairDistill 2410.01383)
- **RAGChecker (arXiv:2408.08067)** — THE big miss. "Almost exactly your proposal, already built and
  validated": claim-level decomposition + entailment checks → diagnostic metrics attributed per module
  (retriever vs generator), incl. a claim-recall = our "had-it-but-missed-it." **Read before writing the
  design; possibly steal metric defs + code.**
- **TREC RAG 2024 nugget eval / AutoNuggetizer** (Pradeep, Lin et al.) — mature "decompose the reference
  into checkable units" with the vital/okay materiality distinction (fixes flaw 2).
- **ARES (arXiv:2311.09476)** — LLM-judge calibration via prediction-powered inference: use the small
  human-labeled pilot set to statistically correct judge bias at scale (fixes flaw 3 when scaling).
- **SAFE / Long-form factuality (arXiv:2403.18802)** — decompose-then-*search*-verify successor to
  FActScore; closer to the oracle-guard step.
- **eRAG (arXiv:2404.13781)** — counterfactual per-document retrieval utility; useful for stage-4
  attribution ("in pool but low rank" vs "top-ranked but not composed").
- **Liu et al. (arXiv:2304.09848)** — grounds oracle citation-quality expectations (~51.5% supported).

## Bottom line (Fable's words)
The idea is good and the instinct to pilot cheap is right. The three things that would have silently
corrupted the results — the circular corpus check, the verbosity penalty, and unvalidated LLM judges —
are all fixable before spending the first $3, and one existing framework (RAGChecker) may hand you half
the build.

# Deep Research — Diagnostic RAG Evaluation (for the Oracle Processing-Debt project)

*Date: 2026-07-06 · Method: deep-research workflow (6 angles → 24 sources fetched → 109 claims → 25 adversarially verified, 23 confirmed / 2 refuted) · Status: input to design*

## TL;DR — there is a ready-made, code-backed stack for exactly what we want
The literature has converged. We do **not** need to invent the measurement; we assemble four existing,
validated pieces and point them at our data + a Brave oracle answer:

| Need | Use | Code | Reusable on our stack (SQLite/sqlite-vec/Ollama)? |
|---|---|---|---|
| "Facts we own but didn't surface" (primary metric) | **RAGChecker** *Claim Recall* | [amazon-science/RAGChecker](https://github.com/amazon-science/RAGChecker) | ✅ Yes — extractor & checker LLMs are swappable (`--extractor_name`/`--checker_name`) → plug in Ollama |
| Materiality (vital vs filler) + verbose-bias fix | **TREC AutoNuggetizer** vital/okay + 3-level support | (TREC RAG) | ⚠️ Adopt the *scheme*, likely re-implement the prompt |
| Retrieval-stage attribution (which chunk carried the fact) | **eRAG** per-document counterfactual utility | [alirezasalemi7/eRAG](https://github.com/alirezasalemi7/eRAG) | ✅ Yes — feed each retrieved doc alone through our LLM |
| Judge trustworthiness | **ARES** PPI calibration on ~50–300 human labels | [stanford-futuredata/ARES](https://github.com/stanford-futuredata/ARES) | ✅ Pattern reusable (calibrate our local checker) |
| Failure taxonomy (labels) | **Barnett Seven Failure Points** | — | ✅ Qualitative checklist only (see refuted note) |

**Bottom line:** RAGChecker with `gt_answer = Brave oracle answer` + a local Ollama judge gives us the
"had-it-but-missed-it" number almost out of the box. This directly de-risks the build Fable flagged.

---

## Verified findings (confidence noted; all 3-0 adversarially confirmed unless stated)

### 1. RAGChecker (arXiv:2408.08067, NeurIPS 2024 D&B) — the primary tool [HIGH]
- Decomposes an answer into **atomic claims**, runs **LLM entailment** checks, emits overall
  precision/recall/F1 **plus per-module metrics**:
  - **Retriever side:** *Claim Recall* (= fraction of ground-truth/oracle claims supported by the
    retrieved context — **this IS our "facts we own but didn't surface" signal**), *Context Precision*.
  - **Generator side:** *Context Utilization, Noise Sensitivity (relevant/irrelevant), Hallucination,
    Self-Knowledge, Faithfulness*.
- **Reuse:** per-question inputs = `query`, `gt_answer`, `retrieved_context` (chunks w/ text), `response`.
  Extractor + checker LLMs independently swappable (RefChecker flags) → **local Ollama judge works**.
  Set `gt_answer` = a Brave-Answers oracle answer → exactly our oracle-vs-system claim-recall setup.
- **Validation:** better correlation with human judgment than BLEU/ROUGE/BERTScore/RAGAS/ARES on a
  280-instance pairwise-preference meta-eval (2 annotators, 5-point scale).
- ⚠️ **Refuted detail:** the precise *dual-entailment* mechanism (entailment against BOTH retrieved
  context AND ground truth) was refuted 1-2 — treat that internal detail as unconfirmed; the top-level
  claim-recall / claim-decomposition behavior is solid.

### 2. TREC 2024 RAG AutoNuggetizer (arXiv:2411.09607) — materiality [HIGH]
- LLMs both **create** nuggets ("few-word to sentence-long" factual units) and **assign** them to answers.
- Each nugget tagged **vital** (indispensable) vs **okay** (helpful-but-non-essential) = the materiality
  filter Fable demanded; each scored **not / partially / fully supported** (3-level, not binary).
- **Validation:** fully-automatic pipeline correlates strongly with the mostly-manual human process
  across **21 topics / 45 runs** → published human-agreement basis for LLM nugget scoring.
- **Use:** score coverage of **vital** nuggets, not answer length → neutralizes Brave's verbosity bias.

### 3. FActScore (arXiv:2305.14251, EMNLP 2023) — atomic-fact lineage [HIGH]
- Break generation into atomic facts; **% supported by a reliable knowledge source**. Ships an
  automated estimator (retrieval + strong LM) **within <2% of the human score** → automated atomic-fact
  scoring can substitute for humans at low error. (Foundational; RAGChecker is the RAG-shaped successor.)

### 4. eRAG (arXiv:2404.13781, SIGIR 2024) — retrieval-stage attribution [HIGH]
- **Counterfactual per-document utility:** feed each retrieved doc **individually** through the RAG LLM,
  score its downstream output vs ground truth → that becomes the doc's utility label; aggregate.
- **Validation:** correlates with end-to-end performance far better than baselines (**Kendall's τ
  +0.168 to +0.494**). Lets us localize *which chunk carried the fact* → separates "in pool but ranked
  low" from "top-ranked but not composed."

### 5. Barnett Seven Failure Points (arXiv:2401.05856) — taxonomy [HIGH, but qualitative]
- Seven empirical failure points from 3 real deployments → checklist for stage labels.
- ⚠️ **Refuted:** the claim that RAG failures cleanly reduce to just retrieval+generation stages was
  refuted 1-2 → use as a **qualitative checklist, not a formal attribution algorithm**.

### 6. ARES (arXiv:2311.09476, NAACL 2024) — judge calibration [HIGH]
- Three axes (context relevance, answer faithfulness, answer relevance) via lightweight LM judges +
  **prediction-powered inference (PPI)** on a small human set (**≥50, ~300 ideal**) → statistically
  **bounded confidence intervals** correcting judge error. This is the recipe to make our local checker
  trustworthy from ~50 hand labels.

### 7. Kappa deflation (arXiv:2606.19544) — report chance-corrected agreement [MEDIUM]
- Across 21 LLM judges on MT-Bench, **raw agreement 0.788–0.851 collapses to Cohen's κ 0.376–0.511**
  (deflation 33.8–41.3 pp). → Report **Cohen's κ**, not raw match, or we'll fool ourselves.
- *Medium* confidence: single recent (2026-06) non-peer-reviewed preprint.

### Supporting (captured in fetch, not in the verified top-10 — treat as leads)
- **Liu et al. 2023 (arXiv:2304.09848):** across Bing Chat / NeevaAI / Perplexity / YouChat, only
  **~51.5% of generated sentences fully supported by citations**, and **74.5% of citations support their
  sentence.** → grounds our **oracle-guard**: expect to drop/flag a big minority of Brave's facts. *(Did
  not survive into the final verified set — the pillar produced no top-10 claim — but the number is from
  the primary source.)*
- **ALCE (arXiv:2305.14627):** citation recall/precision via NLI (TRUE T5-11B) — a concrete oracle-claim
  verification recipe.
- **GroUSE** (144 unit tests, 7 generator failure modes), **ConsJudge** (consistency-based judge DPO),
  and a medical LLM-judge hitting **88.7% agreement vs 3-physician consensus** — evidence a *calibrated*
  entailment judge can match humans on entity/identifier-heavy checks.

---

## Recommended minimal stack for the ~50-question pilot [MEDIUM — a composition, not one cited system]
1. **Primary metric** = RAGChecker **Claim Recall**, `gt_answer` = Brave oracle answer decomposed to
   atomic claims, local Ollama extractor+checker. *Claim present in our corpus but absent from response
   = a surfacing miss.*
2. **Stage attribution** = RAGChecker retriever-vs-generator split (claim IS in retrieved_context but
   missing from answer ⇒ rerank/compose miss; claim NOT in retrieved_context ⇒ retrieval/router miss),
   sharpened by **eRAG** per-doc utility. Label with Barnett's taxonomy.
3. **Materiality** = tag each oracle claim **vital/okay** (AutoNuggetizer) → weight misses by importance,
   kill verbose-vs-concise bias.
4. **Judge reliability** = hand-label ~50–300 entailment decisions, calibrate the local checker
   (ARES/PPI), and **report Cohen's κ** (kappa-deflation warning).

⚠️ **Crucial nuance vs Fable's fix #1 (circular presence check):** RAGChecker's `retrieved_context` is
whatever *our production pipeline* retrieved — so its Claim Recall measures *production* retrieval, which
is exactly the circular trap. **We must add a separate, exhaustive corpus-presence check** (direct SQL +
FTS over ALL types incl. `publication` + brute-force embedding + grep) as a THIRD outcome, so a claim
splits three ways: *in-answer / in-corpus-but-not-surfaced / not-in-corpus*. RAGChecker gives us the
machinery for the first two; the exhaustive check is ours to build.

---

## Honest gaps in this research (from the workflow's own caveats)
- **Two pillars produced NO surviving verified claims:** (a) web-grounded commercial LLMs as oracles
  with measured citation-support + filtering protocols (Liu lineage — captured only as a lead above), and
  (b) **PairDistill / distilling mined preferences into retrievers** (our eventual "fix" phase). Both are
  under-evidenced here and need a follow-up pass *if/when* we get to the distillation phase.
- **No verified evidence on judge reliability specifically for entity-heavy SHORT answers** — our exact
  case (faculty names, titles, orgs). Kappa-deflation data is on general MT-Bench answers. → Our pilot's
  human-adjudication step is how we get this number for our own domain.
- All "reusable on Ollama" assessments are inferred from documented swappable interfaces, not a cited
  demo on sqlite-vec+Ollama specifically.

## Open questions carried forward
1. What citation-support rate does **Brave Answers** actually hit on NJIT questions, and what protocol
   filters unsupported oracle claims before using them as `gt_answer`? (measure in the pilot)
2. Is PairDistill-style distillation of mined router/rerank preferences worthwhile at our scale? (defer)
3. How reliable is our local entailment judge on **entity-heavy short answers**? (measure in the pilot)
4. Smallest human-labeled calibration set for stable PPI on our domain — does 50 suffice, or need ~300?

## Sources (verified primary)
RAGChecker [arXiv:2408.08067](https://arxiv.org/abs/2408.08067) · [code](https://github.com/amazon-science/RAGChecker) ·
AutoNuggetizer [arXiv:2411.09607](https://arxiv.org/abs/2411.09607) · [TREC RAG](https://trec-rag.github.io/annoucements/evaluation/) ·
FActScore [arXiv:2305.14251](https://arxiv.org/abs/2305.14251) ·
eRAG [arXiv:2404.13781](https://arxiv.org/abs/2404.13781) · [code](https://github.com/alirezasalemi7/eRAG) ·
Barnett [arXiv:2401.05856](https://arxiv.org/abs/2401.05856) ·
ARES [arXiv:2311.09476](https://arxiv.org/abs/2311.09476) · [code](https://github.com/stanford-futuredata/ARES) ·
Kappa deflation [arXiv:2606.19544](https://arxiv.org/html/2606.19544v1) ·
Liu et al. [arXiv:2304.09848](https://arxiv.org/abs/2304.09848) ·
ALCE [arXiv:2305.14627](https://arxiv.org/abs/2305.14627) · [code](https://github.com/princeton-nlp/ALCE) ·
PairDistill [arXiv:2410.01383](https://arxiv.org/abs/2410.01383)

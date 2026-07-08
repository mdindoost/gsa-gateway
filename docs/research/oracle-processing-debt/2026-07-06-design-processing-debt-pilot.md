# Design Spec — Processing-Debt Pilot (Oracle-Diagnostic RAG Eval)

**Status:** Design, pending owner sign-off · **Author:** Fable (RAG/LLM review) · **Date:** 2026-07-06
**Type:** Measurement instrument (NOT a fix; NOT a data-import). Pilot phase only.

> Saved verbatim from Fable's design output. Companion files in this folder:
> `PROJECT_MEMORY.md` (state), `2026-07-06-fable-review-oracle-diff-gapfinder.md` (the review that
> preceded this), `2026-07-06-deep-research-diagnostic-rag-eval.md` (the literature basis).

## 0. One-paragraph summary

Use the Brave Answers API as a cheap external **diagnostic oracle** (not a knowledge source) over ~50 real student questions. Decompose each oracle answer into atomic facts, keep only the material (vital) ones, drop the ones the oracle itself can't support from its citations, then classify each surviving fact three ways: **we answered it**, **we own it but didn't surface it** (= processing debt, the gold), or **we don't own it** (= knowledge gap, separate track). The crux and only genuinely custom component is an **exhaustive, non-production corpus-presence check** that is deliberately independent of our production pipeline (so the circular trap is avoided). For every owned-but-not-surfaced fact, attribute the failure to a pipeline stage (router / pool / rank / compose / config) using the `ask.sh` X-ray plus an eRAG per-document utility check. The pilot's primary deliverable is not the debt number — it is **validation of the measurement instrument** (LLM-judge vs human, chance-corrected κ) before any scale-up.

---

## 1. Goals

- **G1.** Produce a **stage-attributed processing-debt report**: % of *material, owned* facts our pipeline failed to surface, sliced by pipeline stage (router / pool / rank / compose / config) and by question stratum.
- **G2.** Build the **exhaustive corpus-presence check** (SQL KG + FTS over *all* `knowledge_items` including excluded `publication` + brute-force embedding scan + raw-text grep), independent of the production retriever.
- **G3.** **Validate the instrument**: 100% human adjudication of pilot fact-judgments; report Cohen's κ (chance-corrected) per decision type; measure oracle correctness on our domain.
- **G4.** Reuse published, code-backed frameworks wherever they fit (RAGChecker, eRAG, AutoNuggetizer protocol, ARES method) rather than re-implement.
- **G5.** Stay within budget: ≤ 50 oracle queries (~$2.85, under the $5/mo free credits and the $30 cap), no bill risk.
- **G6.** Emit a per-fact audit trail (JSONL) so every classification is inspectable and reproducible.

## 2. Non-goals / explicitly deferred (loudly)

- **ND1 — DEFERRED: the "fix" / distillation phase.** No PairDistill-style distillation of mined router/rerank preferences into the retriever. Research returned **no surviving verified claims** here; it needs its own research pass and its own spec. This pilot only *measures* and *localizes* debt.
- **ND2 — DEFERRED: automated scale-up (the other ~450 questions).** Not built until the pilot proves judge-human κ meets gate (§8). ARES prediction-powered-inference calibration is the bridge to scale and is **scaffolded but not run** in the pilot (§6.4).
- **ND3 — NOT a knowledge import.** Nothing the oracle says is written into our KB/KG. The oracle is a diagnostic only.
- **ND4 — Web-needing questions produce no processing-debt number.** By construction their facts aren't owned. They feed only (a) the knowledge-gap track and (b) a side comparison of our live-fallback vs Brave Answers. Kept small in the sample.
- **ND5 — DEFERRED: fixing any bug found.** Findings go into the backlog per the repo hard gate (design → review → owner → TDD). The pilot does not change production code paths.

---

## 3. The 3-way fact classification + exhaustive presence check (the crux)

### 3.1 Pipeline per question

```
Brave oracle answer + citations
  → nuggetize (atomic facts + vital/okay tag)          [materiality filter]
  → oracle-guard (drop facts not supported by their cited page;
                  flag WE_ARE_AUTHORITY on GSA-internal disagreements)
  → keep surviving VITAL (+OKAY, tagged) facts
  → for each fact:
       IN_ANSWER?  (entailed by OUR production answer)   [RAGChecker checker]
         yes → class = IN_ANSWER
         no  → PRESENCE_CHECK (exhaustive, non-production) [OUR BUILD]
                 present → class = OWNED_NOT_SURFACED  → STAGE ATTRIBUTION
                 absent  → class = NOT_OWNED  (knowledge-gap track)
```

### 3.2 The exhaustive presence check — `presence_check.py` (THE crux)

**Design intent:** be *deliberately generous* about declaring "present." We want `NOT_OWNED` to be conservative so processing debt is, if anything, *under*-reported-as-absent — the opposite of the circular trap where the production retriever's blind spots masquerade as missing knowledge. Presence = the fact exists *anywhere* in our data, reachable by *any* means, not "our pipeline retrieves it."

Four independent probes, run as a **union**. Each returns candidate spans; a fact is `present` iff the checker LLM confirms **entailment** between the fact and at least one candidate span.

1. **`kg_probe`** — extract entities (Person/Org/ResearchArea + likely attrs) from the fact; direct **SQL over `nodes`/`edges`** (names, aliases, `attrs` JSON, roles, research edges, profiles/metrics). Catches structured facts the semantic side would miss.
2. **`fts_probe`** — FTS5 `MATCH` over **ALL `knowledge_items`, bypassing `retriever.exclude_types`** — critically including `publication` (normally excluded from the answer corpus). A fact that lives only in an excluded type is *owned* → this becomes the `CONFIG` stage, not a knowledge gap.
3. **`embed_probe`** — embed the fact as a query (`search_query:` prefix, L2-normalized), **full sqlite-vec KNN with generous k (=100), no rerank, low/no threshold**. Catches paraphrase/semantic presence.
4. **`grep_probe`** — plain substring/regex over **raw source text** (`knowledge_items.content`, `title`, and `nodes.attrs` JSON serialized). Catches exact strings that tokenization/embedding drop (odd punctuation, IDs, short names).

`PresenceResult{ present: bool, probes_hit: [str], evidence: [{source_type, row_or_node_id, span, probe}] }`. Evidence rows are retained for stage attribution.

### 3.3 Stage attribution — `attribute.py` (mechanical decision tree)

Inputs: the fact, its `PresenceResult` evidence, and the parsed `ask.sh --answer` X-ray for the question (`xray_parse.py`). One optional eRAG call disambiguates RANK vs COMPOSE.

```
if kg_probe hit AND production router did NOT route to the structured skill that owns it:
      → ROUTER
elif evidence chunk NOT present in the fused candidate pool:
      → POOL
elif evidence chunk in fused pool but below rerank/top-5 cutoff:
      eRAG: feed that chunk ALONE through compose →
        if it would have yielded the fact → RANK
        else → POOL   (chunk is in pool but non-utile; re-classify as retrieval-coverage)
elif evidence chunk in top-5 / context but fact absent from composed answer:
      → COMPOSE   (includes WS4 gate over-abstention; note if gate fired)
elif fact only found via fts_probe/grep in an EXCLUDED type (e.g. publication):
      → CONFIG    (exclude_types decision, surfaceable, not a code bug)
else:
      → UNRESOLVED  (counts against the "≥70% unambiguous" success criterion)
```

---

## 4. Reuse vs build (name each)

| Concern | Decision | How |
|---|---|---|
| Atomic-claim decomposition + IN_ANSWER entailment | **REUSE** RAGChecker / RefChecker | `--extractor_name`/`--checker_name` pointed at local **Ollama**. Produces atomic claims and `response`-vs-`gt` entailment (IN_ANSWER). Its **Claim Recall** is computed too but kept as a **secondary/contrast** signal only (it uses production context = the circular one) — **never the headline**. Note: the internal dual-entailment mechanism was REFUTED in verification; we rely only on the top-level, meta-eval-validated claim/entailment behavior. |
| Materiality (vital/okay) | **BUILD** (thin) following **AutoNuggetizer** protocol (arXiv:2411.09607) | TREC code isn't cleanly packaged; re-implement the nuggetize + vital/okay prompt against Ollama. Small, testable unit. Score coverage of **vital** nuggets → kills Brave's verbosity bias. |
| Per-doc retrieval utility (RANK vs COMPOSE) | **REUSE method + thin adapter** eRAG (github.com/alirezasalemi7/eRAG) | Adapter feeds a single candidate chunk alone through our compose and scores whether the fact appears. |
| Oracle-guard (citation support) | **BUILD** following **ALCE** NLI-citation recipe (arXiv:2305.14627) | Fetch each Brave fact's cited page, NLI-check fact ⊨ page span via the checker; drop unsupported. Plus WE_ARE_AUTHORITY flag on GSA-internal disagreements. |
| Judge calibration for **scale** | **REUSE method, SCAFFOLD only** — ARES PPI (github.com/stanford-futuredata/ARES) | Human-label CSV emitted in the pilot shape ARES expects (≥50 labels). **Not run in pilot** (pilot = 100% adjudicated, so no statistical correction needed to trust pilot numbers). Deferred per ND2. |
| Exhaustive presence check | **BUILD** (the only fully-custom piece) | §3.2. |

---

## 5. Pilot sampling (`sample.py`)

**N = 50 questions**, drawn from `docs/SampleQuestions/`. Pipeline-path label obtained by running `ask.sh` once per candidate and reading the tier verdict.

| Stratum | N | Purpose |
|---|---|---|
| DB-answerable · structured-router-hit | 10 | router/compose debt on structured paths |
| DB-answerable · RAG-answered | 10 | pool/rank/compose debt on semantic paths |
| DB-answerable · live-fallback fired | 6 | did we own it but fall back anyway? |
| DB-answerable · abstained/clarified | 8 | false-abstention (WS4) debt |
| **Positive controls** (we KNOW our answer is complete) | 5 | instrument sanity: method must report ≤1 owned-miss total here, else decomposition/materiality is broken |
| **Oracle-blind controls** (GSA-internal: officers, Wix-sourced) | 3 | guard calibration: oracle should fail/disagree; WE_ARE_AUTHORITY must fire |
| Web-needing (knowledge-gap + live-fallback-vs-Brave contrast) | 8 | ND4 track; no debt number |

**Cost:** 50 × $0.057 ≈ **$2.85** (< 88-query/mo free cap; < $5 free credits; << $30). Oracle responses **cached to disk** — no re-spend on re-runs. All embedding/LLM/guard-fetch work is local/HTTP = free.

Dedupe/cluster the source questions before sampling (real logs are head-heavy) so strata aren't five paraphrases of one intent.

---

## 6. Protocols

### 6.1 Oracle-guard (`oracle_guard.py`)
Per Brave fact: fetch its cited URL (plain HTTP, project UA, **no personal data outbound**), extract text, NLI-check fact ⊨ some span. **Drop** unsupported facts (expect a large minority — Liu 2304.09848: only ~51.5% of generative-search sentences fully supported). If the fact concerns GSA-internal/we-are-authority data and disagrees with our KG → tag `WE_ARE_AUTHORITY`, exclude from debt, log to a "oracle-wrong" tally. **Report the domain oracle-correctness rate**; if > 30% wrong/stale, flag the economics loudly in the report.

### 6.2 Human adjudication (100% of pilot)
Adjudicate every **vital** fact's two primary entailment decisions (IN_ANSWER, PRESENCE) and review stage-attribution on the `OWNED_NOT_SURFACED` subset. Estimated load: ~3–5 vital nuggets/question × 50 ≈ ~200 nuggets → ~400 primary judgments + attribution review — one focused session. `adjudicate.py` emits the CSV and ingests labels.

### 6.3 Judge-calibration metric
Report **Cohen's κ (chance-corrected)** per decision type, not raw agreement — the 2026 preprint (arXiv:2606.19544, medium-confidence) warns raw 0.78–0.85 collapses to κ 0.38–0.51. This is the pilot's headline instrument-validity number.

### 6.4 ARES scaffolding (deferred run)
Emit human labels in ARES's expected format so the scale phase can fit PPI on ≥50 labels. Do not run in pilot.

## 7. Pre-registered success criteria (fix BEFORE running)

- **SC1 (instrument gate).** Cohen's κ on **each** primary decision (IN_ANSWER, PRESENCE): **≥ 0.6 → trust automated scale-up**; **0.4–0.6 → scale only with ARES-PPI correction**; **< 0.4 → method redesign before any scale**.
- **SC2 (positive controls).** ≤ 1 spurious owned-miss across the 5 positive controls. More → decomposition/materiality filter is broken; fix before trusting any debt number.
- **SC3 (guard).** All 3 oracle-blind controls flagged (guard drops or `WE_ARE_AUTHORITY`).
- **SC4 (yield).** ≥ 5 confirmed, stage-attributed, actionable `OWNED_NOT_SURFACED` facts. Below this, the thesis ("weakness is processing") is not demonstrated on real demand — report that honestly.
- **SC5 (attribution).** Stage unambiguous (non-`UNRESOLVED`) for ≥ 70% of confirmed owned-misses.
- **SC6 (oracle).** Report domain oracle-correctness; flag if > 30% wrong/stale.

---

## 8. Outputs (`report.py`)

- **`pilot_report.md`** — headline **Processing Debt** = confirmed `OWNED_NOT_SURFACED` vital facts ÷ (IN_ANSWER + OWNED_NOT_SURFACED) vital facts, with a **per-stage table** (router/pool/rank/compose/config counts + example facts) and a **per-stratum** breakdown; instrument-validity block (κ per decision, control results); oracle-correctness rate; SC1–SC6 pass/fail.
- **`facts.jsonl`** — one record per fact: `{question, stratum, fact_text, vital, guard_verdict, in_answer, presence:{present,probes_hit,evidence}, class, stage, xray_ref}` — full audit trail.
- **`ares_labels.csv`** — human labels in ARES shape (deferred-run bridge).

Every number is tagged **demand-weighted** (oracle-driven, this pilot) to keep it distinct from the corpus-weighted auto-eval harness numbers.

---

## 9. Components / files / interfaces (small, bounded units)

New dir `eval/processing_debt/`:

| File | Responsibility | Key interface |
|---|---|---|
| `types.py` | dataclasses | `OracleAnswer, Nugget, GuardVerdict, AtomicFact, PresenceResult, XRay, Attribution, FactRecord, PilotReport` |
| `oracle_brave.py` | Brave Answers client + disk cache | `ask_oracle(q) -> OracleAnswer{answer, citations[]}` |
| `nuggetize.py` | AutoNuggetizer-style materiality | `nuggetize(OracleAnswer) -> [Nugget{text, vital}]` |
| `oracle_guard.py` | citation NLI + authority flag | `guard(Nugget) -> GuardVerdict` |
| `ragchecker_adapter.py` | RAGChecker/RefChecker on Ollama | `decompose(answer)->[AtomicFact]`, `entails(fact, our_answer)->bool` |
| `presence_check.py` | **the crux** — 4 probes ∪ + entailment | `presence(fact) -> PresenceResult` (probes: `kg_probe/fts_probe/embed_probe/grep_probe`) |
| `xray_parse.py` | parse `ask.sh --answer` | `xray(q) -> XRay{router, fused_pool_ids, ce_scores, top5, tier, answer}` |
| `erag_attrib.py` | per-chunk utility | `utility(chunk, q, fact) -> bool` |
| `attribute.py` | §3.3 decision tree | `attribute(fact, PresenceResult, XRay) -> Attribution{stage}` |
| `classify.py` | orchestrate per-fact | `classify(fact, q) -> FactRecord` |
| `sample.py` | stratified sampler + path labeler | `sample() -> [(q, stratum)]` |
| `adjudicate.py` | emit CSV + ingest labels + κ | `emit_csv(records)`, `kappa(human, machine) -> {decision: κ}` |
| `run_pilot.py` | driver over the 50 | writes `facts.jsonl` |
| `report.py` | aggregate | writes `pilot_report.md` |

**Invariants:** read-only against `gsa_gateway.db`; no writes to KB/KG; oracle responses cached; no personal data in outbound fetches (project UA); does not touch production code paths.

## 10. Test plan (TDD)

- **Unit** — each probe against a **synthetic fixture DB** (planted fact reachable by exactly one probe → asserts union catches it; planted absent fact → asserts `NOT_OWNED`).
- **Golden** — `xray_parse` against a captured `ask.sh --answer` fixture.
- **Mock** — `oracle_brave` HTTP mocked; cache hit/miss tested; zero real spend in CI.
- **Guard** — unsupported-fact fixture dropped; authority-disagreement fixture flagged.
- **Attribution** — table-driven cases for each stage (router/pool/rank/compose/config/unresolved).
- **κ** — synthetic human/machine label pairs → known Cohen's κ.
- **End-to-end** — the 5 positive controls (expect ≤1 owned-miss) and 3 oracle-blind controls (expect guard flags) as an integration gate before the real run.

## 11. Goals shipped-vs-deferred checklist

- [ ] G1 stage-attributed processing-debt report — **in scope, build**
- [ ] G2 exhaustive presence check — **in scope, build (crux)**
- [ ] G3 instrument validation (100% adjudication + κ + oracle-correctness) — **in scope, build**
- [ ] G4 reuse RAGChecker/eRAG/AutoNuggetizer/ARES-scaffold — **in scope**
- [ ] G5 ≤ $3 spend, no bill risk — **in scope**
- [ ] G6 per-fact JSONL audit — **in scope**
- [ ] ND1 distillation/"fix" phase — **DEFERRED (needs own research pass + spec)**
- [ ] ND2 automated scale-up + ARES-PPI run — **DEFERRED (gated on SC1 κ)**
- [ ] ND3 no knowledge import — **enforced invariant**
- [ ] ND4 web-needing → no debt number — **enforced (separate track, N=8)**
- [ ] ND5 no production fixes in this phase — **enforced**

---

**Recommendation:** proceed to build this pilot after owner sign-off. It is measurement-only, ~$3, reversible (read-only), and its first job is to prove its own trustworthiness (κ) before any conclusion — including the owner's core "processing not knowledge" thesis — is asserted. Two things to flag to the owner in plain terms: (1) full 2000-question scale was never affordable at $0.057/q — realistic reach is pilot + ~450 questions, and scale is gated on the pilot's κ; (2) expect the oracle to be wrong a meaningful fraction of the time on NJIT specifics, which is why the guard and human adjudication are non-negotiable, not optional polish.

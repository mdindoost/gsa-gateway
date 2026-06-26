# Org-Assistant Answer Stack + Continuous-Curation Cycle — Design

**Date:** 2026-06-26
**Status:** DRAFT — brainstorming complete; awaiting expert reviews (senior-eng + RAG) + owner approval per the EXPERT-REVIEW HARD GATE.
**Branch context:** sibling to `feat/durable-retrieval-foundation`. This spec is the *strategy layer* that reframes and absorbs the chunking work (which hit a NO-GO at cutover — see §9).

---

## 1. Why this exists (the reframe)

The durable-retrieval/chunking effort proved (3.7× deep-recall lift) but **regressed the common short-item case 84% → 77%** on `eval.sh`, and the regression survived every fusion wiring tried (replace / augment / carve / widen all landed 76–78%). Reject-criterion #1 was not met → **NO-GO for always-on chunking.**

The owner's instinct: *our situation is not the general RAG problem.* We are a **small (~6k servable docs), bounded, mostly-static, single-domain (NJIT)** assistant — and **production org/enterprise assistants (Intercom Fin, Glean, kapa.ai, etc.) reliably answer this exact shape of problem.** So there is a known way; we should adopt it rather than re-derive it.

A deep-research pass (2026-06-26, 17 adversarially-verified findings, 8 killed) confirmed the path. The key evidence:

- **Content quality is the #1 accuracy lever, above retrieval cleverness.** Intercom Fin docs: *"If it's confusing for a human to read, it'll be confusing for an AI agent as well"*; segment content into focused single-topic header-bounded sections. kapa.ai (100+ deployments): *"RAG quality is bounded by knowledge-base quality."*
- **The converged retrieval architecture is the one we already have:** hybrid BM25 + vector → RRF (k≈60) → cross-encoder rerank → ~5–12 final. We do **not** need to reinvent retrieval.
- **Retrieval precision + the model's noise-robustness dominate — not generator size.** Irrelevant/noisy context significantly degrades answers; without retrieval robustness, quality is capped by the retriever.
- **Small local models fail *worst* exactly when handed noisy/insufficient context** (Gemma incorrect-rate 10%→66% with insufficient context; RAG paradoxically reduces willingness to abstain). **This is precisely our chunk regression.** The fix the industry uses is not cleverer chunking — it is *clean content units + precision + confidence-gating* so the small model is never handed noisy context.

**Conclusion:** stop fighting the published chunking tradeoff. Build the proven org-assistant pattern, exploiting "static + bounded + small" as an asset (lavish one-time curation, no churn machinery).

Full evidence: deep-research report (this session) + `docs/superpowers/specs/2026-06-25-durable-retrieval-foundation-design.md` §2.

---

## 2. The architecture — a confidence-graded answer stack

Five tiers. Each owns a query segment. Ordered so the small local generator is only asked to compose when the context in front of it is clean (the #1 protection against small-model hallucination).

| # | Tier | State | Owns | LLM role |
|---|---|---|---|---|
| 1 | **Structured / skills** | exists | KG facts: faculty, people, metrics, officers, office-routing | none — route-or-None (no confidence score; first-wins by design) |

> **Live-dispatch reality (SE review):** production runs `ROUTER_V21=1, ROUTER_V21_SHADOW=0` — the live
> dispatcher is the **UnifiedRouter** + `MessageHandler._answer_decision` (families COMMAND/KG/RAG/LIVE/
> CLARIFY), **not** the legacy `router.py`/`_try_structured`. Tier 1 = the KG family; **C (tier 2) = a new
> `CURATED` family resolved in `decide()` / a pre-RAG check**. G1 is therefore a **refactor of the branchy
> `handle()`/`_rag_pipeline`** (INTENT_FOOD/SOCIAL, free-mode skip, deflection-offer, live-fallback) into the
> ladder — NOT mere "wiring." The vestigial local `office_page` tier (0 live rows) is DELETED, not extended.
| 2 | **C — Curated golden answers** | **new** | bounded high-stakes *procedural* head (I-20/visa, billing, registration holds, funding deadlines, full-time rule, test/English rules) | rephrase only; facts authored |
| 3 | **A — Structured-clean RAG** | improved | the prose tail + program/advising/policy + the floor under C | compose from clean sections |
| 4 | **Deep-fallback / live njit.edu** | exists, repositioned | low-confidence KB-miss rescue | extractive / grounded |
| 5 | **Deflect honestly** | exists | nothing clears the confidence bar | none (route to office) |

**Design principle — isolation:** each tier has one purpose, a defined input/output, and is independently testable. A query flows top-down; the first tier that answers *with sufficient confidence* wins. Confidence-gating between tiers is what protects tier-3's small generator.

### 2.1 Tier ownership = the honest weighting

The weight of A vs C is **not a single ratio** — they own different query segments:

- **Structured tier** carries the largest volume slice (faculty/person/metric/office) and is already deterministic.
- **C** is a *small, high-value patch* (~8–10 authored answers) on the procedural intents where a wrong/partial answer hurts a student most *and* where noisy RAG + small model fails worst. Bounded; not a 50/50 partner with A.
- **A** is the broad, durable spine over everything else, with **zero ongoing labor** (one-time content cleaning at ingest).

We deliberately do **not** assign a measured `C = X%` weight up front: the only traffic data we have (~920 logged questions) is ~83% the maintainer's own dev/test traffic, so it is **not** a demand signal. C's initial scope comes from the owner's authored **150-intent A–O catalog** (authoritative domain knowledge), and the *real* weight reveals itself through the cycle (§4).

---

## 3. The three lanes and the content rule

- **Structured lane** — KG/SQL skills. Unchanged.
- **C / curated lane** (`source='dashboard'`) — authored golden answers, same provenance lane as GSA officer prose. Authoring an answer *from a source + link* is allowed here (it is NOT the crawler, so the "mechanical-clean only / no rewriting" hard line does not apply — that line governs the crawler lane only).
- **A / crawler lane** (`created_by` crawler/college_crawl) — mechanical-clean only. **Section structuring (header-bounded splitting) is structural, not rewriting → permitted.**

**Content-sourcing rule (resolves "manual vs crawler?"):**
1. **Default: crawler.** If the content lives on a crawlable NJIT HTML page → append one `ProseEntry(seed, org_slug, …)` to `PROSE_ENTRY_POINTS`. No new code. Auto-refreshes on recrawl. Keeps "the crawl is the one source."
2. **Manual** (`source='dashboard'`) **only** when there is no crawlable page — PDF-only (deferred PDF-extraction item), authenticated portal, or genuine tribal knowledge.

---

## 4. The continuous-curation cycle (the engine)

The corpus is fixed, but **we don't know which parts students will hit** — so the real questions direct curation effort. A closed loop:

1. **Instrument** — log every real question with: text, answering tier, confidence, sources used, outcome (answered / deflected / 👎). *Closes the `was_answered`-never-set gap.*
2. **Observe** — periodically cluster logged questions; surface (a) **recurring intents** (emerging head) and (b) **failures** (deflects, low-confidence, 👎, partials).
3. **Triage with the §3 rule** — classify each recurring gap: *content gap* → crawler seed/catalog; *serving gap* → A retrieval tuning; *high-stakes precision gap* → author a C golden answer.
4. **Fix** — gated, reversible (`hardened_backup`, dry-run default).
5. **Lock it in** — the failing question becomes a permanent regression line in `eval/questions.txt` (existing hard line: every change grows the correctness suite).
6. **Repeat.**

**Result:** C self-scopes — starts from the A–O catalog, grows toward what students actually ask, and *stops* when the head is covered (diminishing returns visible in the data). The weight follows the data instead of being guessed.

**Reuses existing pieces** (wire into an explicit loop, don't rebuild): `questions` table (logging), `feedback`/`response_feedback` (👎), `eval/questions.txt` (regression), dashboard Analytics tab, `eval.sh` (measurement). Three gaps to close: unused `was_answered`, no clustering/gap view, no triage workflow.

This loop mirrors how kapa.ai / Intercom operate (content-gap report → human adds content).

---

## 5. Coverage map — the 150 intents (A–O) vs current live DB

Verified against the live DB 2026-06-26 (13 offices, 6 colleges, 16 departments, 21,808 active KB items).

| Cat | Intent | Content status | Owner |
|---|---|---|---|
| A | Admissions | ✅ covered | A-RAG + structured |
| B | Application status | ⚠️ often personal/live | route to office |
| C | Test scores / English | ✅ covered | **C golden** |
| D | I-20 / visa | ✅ covered (OGI) | **C golden** (high-stakes) |
| E | Offer / deferral / enrollment | 🔴 thin → crawler seeds (resolved §5.1) | A-RAG → C |
| F | Funding / TA / RA | ⚠️ partial | **C golden** + A-RAG |
| G | Tuition / billing | ✅ covered (Bursar) | **C golden** (high-stakes) |
| H | Registration | ✅ covered (Registrar) | **C golden** (holds) + A-RAG |
| I | Full-time status | ⚠️ partial | **C golden** (one rule) |
| J | Advising / program reqs | ⚠️ partial (dept prose) | A-RAG (tail) |
| K | Transfer credits | ✅ present (52 hits) — serving, not content | A-RAG |
| L | International / CPT / OPT | ✅ covered (OGI) | **C golden** (high-stakes) |
| M | Campus offices / routing | ✅ covered | structured (+ dilution fix) |
| N | Master's thesis | ✅ present (340/118 hits) — serving, not content | A-RAG (+ C for deadlines) |
| O | PhD milestones | ⚠️ partial | A-RAG + thin spots |

**Most categories already have content** (the office + college rollout filled the gap the original A–O memory predicted). The remaining work is mostly *serving* (A) + a small set of *authored* high-stakes answers (C), not new data gathering.

### 5.1 E resolved (the only genuine content gap)

Both pieces are crawlable HTML (no portal/PDF), confirmed 2026-06-26:
- Enrollment deposit / accept-offer → `njit.edu/admissions/admitted-students` (graduate section).
- Deferral rule → `catalog.njit.edu/graduate/admissions-financial-support/admissions/` (*"Applications may be deferred for one semester … without incurring another fee"*).
→ **Crawler, 2 seeds.** Manual not needed.

**`catalog.njit.edu` — a SCOPED addition, NOT a free bonus (SE review, biggest landmine).** The grad catalog reinforces J/K/N/O, but it does NOT fit the `college_crawl` engine's single-org bare-host assumption: it is **cross-org** (spans all programs → filing under one `org_id` would WORSEN the M routing dilution §7/G7 is fixing), **thousands of pages** (default `crawl_entry budget=400` would silently truncate), and **grid-heavy** (program/course tables = the dense-retrieval-weak case). Required before adding: scope by path into per-program sub-entries OR a dedicated `catalog` org that makes **no org-scoped routing claim**; raise/parametrize the budget; run through the grid detector; and **re-pass the held-out routing gate** (it is a corpus addition, not free). Deferred to its own gated sub-plan.

---

## 6. What changes vs. what stays

**Stays (proven):** the hybrid BM25 + vector + RRF + cross-encoder rerank pipeline (research-confirmed correct); the structured/skills tier; the live-fallback; the gated/dev-copy/backup workflow; LLM-agnostic + use-max-capacity hard lines.

**Changes / new:**
- **A:** section-structure + junk-carve the crawled corpus at ingest (**mechanical only** — nav/boilerplate/asset strip + structural header-split; NEVER meaning-based section dropping, per the crawl hard line). Sections are for FINDING; they **collapse-to-parent-by-best-child BEFORE fusion** (reuse `_semantic_chunks` min-rank collapse) so the count of competing units is bounded back to parents — this is what prevents reintroducing the 84→77 cross-doc dilution. Chunks repositioned as **deep-fallback only** (tier 4). **NOTE (both reviews): "sections recover the points" and "chunks-as-fallback can't regress" are GATE-CONDITIONAL HYPOTHESES, eval-gated (reject #1) before flip — NOT structural guarantees.** They hold only if the A→tier-4 fire decision is calibrated (see §10.1).
- **C:** new curated golden-answer tier + intent match + confidence-graded placement above A.
- **Cycle:** instrument + clustering/gap view + triage workflow (close the 3 gaps).
- **Routing:** fix the M office-dilution regression via the structured/office-prior mechanism (from the durable-foundation spec; held-out gate, not spot-checks).

**Explicitly deferred (flagged, not dropped):** stronger local generator (optional knob, not a substitute — evidence: size alone insufficient); grid carve-out precision detector; Contextual Retrieval pilot (fenced, off by default).

---

## 7. Relationship to the durable-foundation work

This spec **supersedes the framing** of the durable-foundation/chunking spec, not its built artifacts. Plan 1 (chunk tables + vector-GC) and Plan 2 (descriptor + chunker + batch embed) remain valid and reusable — chunks now serve **tier 4 (deep-fallback)** instead of always-on. The G2 office-prior and held-out office set carry over to §6 routing. Nothing built is thrown away; the always-on chunk leg is what's dropped.

---

## 8. Goals checklist (shipped / deferred — to be maintained through build)

- [ ] G1 — Confidence-graded answer stack (tiers 1–5) wired, first-confident-wins.
- [ ] G2 — C golden-answer tier + intent match + ~8–10 high-stakes procedural answers from A–O.
- [ ] G3 — A: section-structure + junk-carve at ingest (mechanical, crawler-lane).
- [ ] G4 — Chunks repositioned to deep-fallback (tier 4); no common-case regression (eval.sh ≥ baseline 84%).
- [ ] G5 — Continuous-curation cycle: instrument (fix `was_answered`) + clustering/gap view + triage workflow.
- [ ] G6 — Content rule applied: E crawler seeds + `catalog.njit.edu` seed family.
- [ ] G7 — M office-dilution routing fix with held-out gate.
- [ ] Deferred (loud): stronger generator knob; grid-detector precision; Contextual Retrieval pilot.

---

## 9. Reject criteria (must hold before any cutover)

1. The stack must score **≥ current 84%** on `eval.sh` (no common-case regression — the thing always-on chunking failed).
2. C golden answers must be faithful to source + carry the link (never contradict NJIT content; honors verbatim/never-withhold).
3. Confidence-gating must measurably reduce forced-hallucination on low-confidence queries (deflect rather than confabulate).
4. Every change gated + reversible; immortal posts/judging untouched.

---

## 10. Open questions (for expert review)

1. Confidence signal for tier-gating — reuse the live-fallback rerank-relevance threshold, or a calibrated per-tier score? (Needs the CE scores threaded out of `_rerank`.)
2. Intent-match mechanism for C — deterministic router cues, embedding-NN over intent descriptions (cf. the SemanticOfficeClassifier built for G2), or a small grounded-JSON classifier? Build-correct, not old-router-constrained.
3. Cycle cadence — manual/periodic (dashboard tab) vs scheduled job? Start manual.
4. Does C live as `knowledge_items` (so it flows through the same retrieval + link rendering) or a separate golden-answer store consulted before RAG? Leaning: `knowledge_items` with a high-priority type, to reuse infra.
5. Section-structuring granularity — header-bounded sections (Intercom) as the retrieval unit for A, vs whole-doc. (The durable-foundation eval suggests medium-grained sections, not heavy sub-doc chunks.)

---

## 11. Build sequencing (proposed — for writing-plans)

Gated, flag-behind, eval-before-cutover. Migration/serving de-risked first.

1. **Cycle instrumentation** (lowest risk, immediate value): fix `was_answered`, log answering-tier + sources, dashboard gap view. Ship vs current DB.
2. **A: section-structure + junk-carve** at ingest (mechanical), behind A/B; prove eval ≥ 84%.
3. **C: golden-answer tier** + intent match + author the ~8–10 high-stakes A–O answers; per-intent gold gates.
4. **Tier wiring + confidence-gating** (first-confident-wins; chunks → deep-fallback); full eval.sh A/B.
5. **Routing fix (M)** with held-out office gate.
6. **E crawler seeds + `catalog.njit.edu`** seed family.

Each step: senior-eng + RAG review where it touches retrieval/answers (HARD GATE), owner approval, TDD.

---

## 12. Review folds — both HARD-GATE reviews (2026-06-26)

Senior-eng: **GO-WITH-CHANGES.** RAG/LLM-researcher: **GO-WITH-CHANGES.** Both factual claims verified
against the live system before folding (ROUTER_V21=1, office_page=0 rows, was_answered never written,
log_question 15 call sites). All required changes accepted; nothing rejected. Resolutions:

**Architecture / integration**
- **R1 (SE) Live-router reality** — tier framing rewritten around the UnifiedRouter; C = new `CURATED`
  family; G1 = explicit `_rag_pipeline` refactor; dead `office_page` tier deleted. (Folded §2.)
- **R2 (RAG) "First-confident-wins" conflates two decision types** — tiers 1–2 are **precision GATES**
  (route/intent match, abstain-to-next), tier 3→4→5 is the **CE-relevance-graded** boundary. Reframed:
  not a homogeneous confidence ladder. Tier 1 has no score (route-or-None). (Folded §2 note.)

**Confidence signal (R3 — reconciled SE+RAG)**
- A gating signal already exists (`top_relevance` = a 2nd CE call) — so it's perf/accuracy, not pure
  plumbing (SE corrects RAG). Fold: thread the per-item CE score computed in `_rerank` onto a new
  `RetrievedChunk.ce_score` (free; avoid the double CE pass); when chunks on, score the **matched-chunk
  passage** (`_chunk_passage`), not the CE-truncated full doc; **gate on CE, NEVER on llama self-report**;
  **calibrate per-tier thresholds on a labeled set** (do NOT inherit `LIVE_THRESHOLD=0.15`). New goal G8.

**Curated tier C**
- **R4 (RAG+SE) C = SEPARATE store + deterministic high-precision intent gate, abstain-to-A** (pre-empt
  semantics; resolves the §2-vs-§10.4 contradiction — NOT `knowledge_items`). Intent match = **deterministic
  cues primary** (versioned data, auditable), embedding-NN only as a confirm-router, **reject the small-model
  grounded-JSON classifier** for this safety gate.
- **R5 (SE+RAG) C verbatim + staleness — highest-risk gap, both flagged.** Load-bearing figures (credits/
  fees/deadlines) appended VERBATIM + source link via the `deterministic_suffix` pattern; llama "rephrase"
  step suppressed on them (mirror `_compose_structured` deterministic). Add a **C-re-verify-against-source**
  step to the cycle (content-hash the source span, flag drift) — C is manual and will NOT auto-refresh like A. New goal G9.

**Section-structuring / chunks**
- **R6 (RAG) Section-structuring is the riskiest unproven claim** — downgraded to eval-gated hypothesis;
  **collapse-to-parent-before-fusion mandated**; A/B section-on vs whole-doc in isolation. (Folded §2/§6.)
- **R7 (SE) Chunk-table precondition** — tier 4 requires durable-foundation **Plan 1+2 built + chunk
  re-embed done on the LIVE DB**; added as a hard precondition to build step 4 (§11).

**Measurement integrity (RAG — make the gate honest)**
- **R8 Frozen held-out eval slice** never used to author C / tune A; reject #1 measured on it. Extend the
  §7 held-out discipline to the WHOLE cycle (stop self-fulfilling ≥84).
- **R9 Quantify auto-judge variance** (baseline ×2–3, measure σ); a claimed win must exceed σ.
- **R10 Define the abstain/hallucination instrument** (unanswerable/adversarial set scored on
  abstain-correctness) — else reject #3 is unfalsifiable.
- **R11 Expand `eval.sh`** to include deep + high-stakes + office-intent questions BEFORE build, so the
  design's real (off-common-eval) gains are visible. EV stated honestly: **"≥84 safely + better on
  deep/high-stakes/routing + a durable curation engine,"** not "beats 84 on the common eval."

**Cycle / instrumentation**
- **R12 (SE) Instrumentation enumerated** — add `answering_tier` + `sources` columns, fix `was_answered`,
  change `log_question` signature + ALL call sites, unify the 4 inconsistent `confidence` scales; gap-view
  runs on a SEPARATE READ connection, never inline in the hot path.
- **R13 (SE+RAG) Tag dev-vs-real-user traffic**; mine only non-maintainer post-launch questions for the
  demand signal; A–O catalog stays the primary scope driver until real traffic is significant.

**Corpus re-gating**
- **R14 (SE) Re-run the held-out routing gate after ANY corpus change** (build steps 2 AND 6), not once at
  step 5. catalog/E seeds + the A re-chunk all add dilution after the gate.

### Goals added (extend §8)
- [ ] **G8** — Calibrated CE-based per-tier confidence gate (free `ce_score`, matched-chunk passage, labeled-set thresholds); no tier wiring until it exists.
- [ ] **G9** — C verbatim-figure append + C-staleness re-verify loop.
- [ ] **G10** — Honest measurement: frozen held-out slice, judge-σ, abstain instrument, expanded `eval.sh`.

### Reject criteria revised (§9)
- #1 ≥84% measured on the **frozen held-out slice**, and any win must **exceed judge σ**.
- #3 measured via the **defined abstain/hallucination instrument** (else unfalsifiable).

### Build order revised (§11)
Insert **step 0: build + calibrate the CE confidence gate (G8) and expand `eval.sh` + held-out slice (G10)**
FIRST — the gate is the most load-bearing, least-evidenced piece, so it leads. Step 4 precondition:
durable Plan 1+2 + live chunk re-embed (R7). `catalog.njit.edu` is its own gated sub-plan (R2/§5.1), after
the routing gate, re-running it (R14).

---

## 13. Calibration findings + revised gating design (2026-06-26, evidence-backed)

Step 0 built the signal (0a CE `ce_score`) and the measurement harness (0b). We then MEASURED the
baselines and CALIBRATED the gate empirically (LIVE off, chunks/prior off = current prod). The results
**reshape the tier-5 / confidence-gating design** — recorded here as the source of truth for the build.

### 13.1 Measured baselines
- **Visible eval: 84% correct** (189/36 partial/1 wrong of 226) — confirms the reference exactly.
- **Judge variance: σ = 0.88 → 2σ ≈ 1.8 pts.** A real A/B win must exceed ~1.8 pts. (The old 84→77 chunk
  regression = 7 pts = unambiguously real.)
- **Held-out slice: 57% correct** (4/3/0 of 7) — deep/high-stakes-heavy, dragged down by deep partials.
- **Abstain: 0/7 deflected = 0%** — the bot confabulated an answer to EVERY unanswerable question, incl.
  "what is my financial aid balance" and "has my I-20 been approved." Forced-hallucination = measured, 0% gate.

### 13.2 CE-relevance threshold FAILS for abstain (RELEVANCE ≠ ANSWERABILITY)
Top `ce_score` (sigmoid 0..1) does NOT separate abstain from answerable — overlaps both ways:
abstain "has my I-20 approved" = **0.993**, "YWCC last week" = 0.708, "my balance" = 0.282 (HIGH — CE finds the
TOPIC relevant though the ANSWER is in no document); meanwhile 16 real answerable Qs score < 0.15. Sweep:
catching half the abstain set costs 7%+ real answers; I-20=0.993 uncatchable at any usable T. **A CE floor is
the WRONG tool for abstain.** (0a `ce_score` REMAINS valid for its real jobs: live-fallback ranking + the
deep-fallback decision. Not wasted — boundary learned.) **This supersedes the §10.1 framing that tier-gating =
a CE-relevance floor.**

### 13.3 LLM gate measured (answers "use the LLM for both?")
Single 8B call (intent+answerability) vs 7 abstain + 25 real RAG-answerable, 2 runs:
- **0 false-deflects on real answers, BOTH runs (25/25).** Far safer than CE (which false-deflected 6-7%).
- Caught 4–5/7 abstain — the not-in-corpus / out-of-scope ones (Rutgers, homecoming, "write my SOP", "menu today").
- **LEAKED the personal/live ones (my balance, my I-20, last-week meeting)** — same blind spot as CE: the topic
  is in context, so "is the answer here?" says yes; the model can't see "this is the USER'S OWN private/live state."
- Mild run-to-run noise (4 vs 5 = small-model σ).

### 13.4 REVISED tier-5 design — HYBRID two-gate (replaces "CE threshold")
The two gates have **complementary blind spots**, so neither alone suffices; together they catch all 7 abstain
with **0 real-answer damage**:
- **Gate 1 — deterministic INTENT cues, PRE-retrieval.** First-person-possessive about a personal/account record
  ("my balance", "has my I-20"), live/time-specific ("today/tonight/this week/current"), other-institution
  ("Rutgers/Princeton/…"), do-a-task ("write my…"). Cheap, deterministic, zero LLM noise. Catches the cases the
  LLM is blind to. (Plays to the deterministic router's strength — [[feedback_build_correct_not_router_constrained]].)
- **Gate 2 — LLM ANSWERABILITY check, POST-retrieval.** "Does the retrieved context actually contain the answer?"
  8B is GOOD here (25/25 real, 0 false-deflect). Provider-isolated (LLM-agnostic). Cost = +1 8B call on the RAG
  path, justified by 0 false-deflect. A/B-gated on the abstain set before trusting; a stronger judge model is the
  escalation if 8B noise hurts.
- Net target: see §13.6 — the "~100% abstain / 0 false-deflect / 84% unharmed" framing here is from an
  UNDERPOWERED pilot (n=7 abstain / n=25 real); both HARD-GATE reviews corrected it to a CONSTRAINED JOINT
  metric measured on a frozen held-out instrument. §13.6 is authoritative.

### 13.5 Build implication
- **G8 REVISED:** the gate is NOT a CE threshold. It is (Gate 1) a deterministic abstain-cue set + (Gate 2) an
  LLM answerability check, each measured on the abstain set + held-out. The 0a `ce_score` is retained for
  live-fallback ranking and the deep-fallback trigger, NOT for abstain.
- This is a retrieval/answer-path design change → **HARD GATE: senior-eng + RAG review of the revised gating
  design + owner approval BEFORE building it into the live message path.** The probes above are read-only;
  nothing is wired into production yet.

---

## 13.6 Both HARD-GATE reviews folded — revised two-gate build contract (2026-06-26)

Senior-eng: **GO-WITH-CHANGES.** RAG/LLM-researcher: **GO-WITH-CHANGES.** Strong CONVERGENCE; nothing rejected.
Both endorse the hybrid two-gate DIRECTION and the relevance≠answerability lesson (RAG ties it to the published
sufficient-context / Self-RAG ISSUP / CRAG / RAGAS line). Both block on the SAME thing: **the evidence is an
underpowered pilot and the gate must be SHADOW-measured at scale before it touches the 84% path.** This §13.6 is
the authoritative contract; it supersedes the optimistic numbers in §13.3–13.4.

**Self-correction (owned):** "0 false-deflect on 25/25 → safe" is wrong. Rule of three: 0/25 ⇒ true false-deflect
rate up to ~11–12% (95% CI) → up to ~22 wrongly-deflected real answers → would blow reject-#1 AND the
never-withhold hard line. n=7 abstain = in-sample curve-fit.

### Consolidated required changes (fold BEFORE building into the live message path)
1. **Frozen held-out instrument, much larger (both).** Abstain/adversarial **≥50–100**, stratified by failure
   class: (a) personal/account state, (b) live/time-specific, (c) other-institution, (d) do-a-task, (e)
   out-of-scope topic, (f) **in-domain-but-not-in-corpus** (hardest/most important), (g) ambiguous, (h)
   multi-hop-insufficient — PLUS answerable near-miss FP traps per class. Real-answerable **≥150 clean**
   (for false-deflect <2% at 95% CI by rule-of-three), **stratified to include the 36 partials + deep Qs**
   (where a strict gate actually bites). Frozen — never used to tune cues/prompt. Reject-#1/#3 measured here.
2. **SHADOW-measure Gate 2 first (read-only) over the full 226 eval + held-out (both).** Log the verdict,
   DON'T act; for every currently-correct/partial answer, record what Gate 2 WOULD do = the false-deflect cost.
   Build-gate = correct/partial false-deflect ≤ ~1–2 pt (inside σ). This is the load-bearing, free measurement.
3. **Gate-2 prompt = closed-book, evidence-first, GRADED (RAG — biggest leakage fix).** Require a VERBATIM
   supporting span (plural spans / entailment allowed — do NOT demand exact-substring, or partials/multi-hop
   false-deflect) BEFORE a label `{FULLY_SUPPORTED, PARTIALLY_SUPPORTED, NOT_IN_CONTEXT}`; JSON
   `{supporting_quote, label, missing_piece}`. Cite-or-abstain converts "topic present→answerable" vibes into
   grounded verification. Gate 2 carries answerability ONLY — never intent (that's Gate 1's job).
4. **Gate ordering = never-withhold (both, hard line).** Gate-2 `NOT_IN_CONTEXT` is the **deep-fallback (tier4)
   → live njit.edu → deflect-ONLY-if-all-miss** trigger — NEVER a terminal deflect (a grounding miss ≠ NJIT
   lacks it). `ce_score` (retained per §13.5) plugs in as the deep-fallback decision. Gate-1 hits (personal/
   account/live/other-institution/task) deflect IMMEDIATELY and SKIP fallback (no public page states a per-user
   balance; fallback would risk confident-wrong).
5. **Gate-1 precision (both).** HARD cues only: possessive **+ personal-record-noun** (`my {balance, hold, I-20,
   application status, refund, transcript, GPA, award}`), NEVER bare `my`; do-a-task verbs (`write/draft/fill out
   my…`); other-institution names (exempt "transfer FROM X"). Time-cues (`today/tonight/current`) must co-occur
   with a personal/live referent — never fire alone (carve out events/food: INTENT_FOOD legitimately uses
   "today"). Verify **≈0 Gate-1 false-fire on the real-answerable set** (a false-fire withholds real content =
   hard-line breach). FP traps ("what are MY responsibilities as a club officer", "what events are TODAY",
   "transfer from Rutgers") → held-out regression lines.
6. **Constrained JOINT metric, not abstain-correctness alone (both).** Deflect-everything scores 100% abstain /
   0% coverage. Objective = **maximize abstain-correctness SUBJECT TO common-case ≥ 84 − 2σ (≈82.2, ideally ≥84)
   AND false-deflect ≤ ~1–2 pt.** Report the full 2×2 (true/false × deflect/answer); weight false-deflect as a
   near-veto (hard-line breach). Accept a documented residual on the hardest personal/live homonyms rather than
   chase 100% into the false-deflect zone.
7. **Exact integration seams + exemptions (SE).** Gate 1 = new `DEFLECT` family in `UnifiedRouter.decide()` AFTER
   `command_layer` / before `fast_path`, **mode-aware** (gsa-only; suppress office wording in free mode); add the
   `fam=="DEFLECT"` branch in `_answer_decision`. Gate 2 = in `_rag_pipeline` AFTER office/live-fallback
   resolution / BEFORE `generate_answer`, set `is_canned_deflection=True` on deflect (reuse the offer/log path).
   **EXEMPT from Gate 2 entirely:** KG/structured, `used_live`, INTENT_FOOD/SOCIAL, and **C golden / any
   `is_deterministic` answer** (an 8B grounding check must never second-guess authored/deterministic facts).
   Delete the vestigial `office_page` tier (0 rows).
8. **Gate-the-gate + noise + LLM-agnostic (both).** Run the LLM Gate 2 ONLY in a calibrated AMBIGUOUS `ce_score`
   band (skip the confident-high end — the free 0a signal), to avoid a 4th serial 8B call on every RAG query;
   budget cumulative RAG-path LLM calls + p50/p95 latency. Gate 2 at **temp 0 + constrained output + answer-
   biased default** (act only on confident NOT_IN_CONTEXT). Single-call first; escalate to k=3 majority OR a
   stronger judge ONLY if measured flip/false-deflect exceeds tolerance — escalation model stays **local /
   provider-isolated** (LLM-agnostic hard line).

### Goals added
- [ ] **G11** — Gates measured on full 226 eval + ≥50–100 frozen held-out abstain + ≥150 real (stratified incl.
  partials/deep); Gate-2 shadow-measured; **build-gate = common-case ≥ 84 − 2σ AND false-deflect ≤ ~1–2 pt**;
  reject-#1 RE-MEASURED gate-ON (not assumed). Gate-1 false-fire ≈0 on the real set.
- [ ] **G12** — Gate-2 evidence-first graded prompt (quote→label JSON, plural-span/entailment), temp 0,
  answer-biased, gate-the-gate on `ce_score`, NOT_IN_CONTEXT→fallback→deflect ordering, deterministic/C exempt.

### Status
Direction CONFIRMED by both reviews. **Build = the gate + the measurement harness, SHADOW first; DO NOT cut over
to the live message path until G11's bars (changes 1, 2, 4, 6) are met on the frozen slice.** Probes so far are
read-only; nothing wired. Next: owner sign-off on this revised contract → plan the gate + harness build (TDD,
shadow-mode) → measure → owner cutover gate.

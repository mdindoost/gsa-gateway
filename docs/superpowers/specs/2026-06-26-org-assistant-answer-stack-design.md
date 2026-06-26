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
| 1 | **Structured / skills** | exists | KG facts: faculty, people, metrics, officers, office-routing | none (deterministic) |
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

**Bonus seed family:** `catalog.njit.edu` (the graduate catalog) is fully crawlable, authoritative, and currently un-crawled — it reinforces J / K / N / O at once. Recommend adding it as a `ProseEntry` family in the rebuild.

---

## 6. What changes vs. what stays

**Stays (proven):** the hybrid BM25 + vector + RRF + cross-encoder rerank pipeline (research-confirmed correct); the structured/skills tier; the live-fallback; the gated/dev-copy/backup workflow; LLM-agnostic + use-max-capacity hard lines.

**Changes / new:**
- **A:** section-structure + junk-carve the crawled corpus at ingest (mechanical). Fold in the durable-foundation cleaning. Chunks repositioned as **deep-fallback only** (tier 4) — structurally cannot regress the common case, since it never touches questions the normal pipeline already answers.
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

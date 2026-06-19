# Scoped-Comprehensive NJIT Grad-Content Crawl + Reusable Pipeline — Design

**Date:** 2026-06-18
**Status:** PROPOSED (for senior review before build)
**Task:** #5. Authorized by Mohammad: ingest everything grad-students need that's on njit.edu, autonomously + gated, **high-stakes facts staged for his eyes**.
**Relates to:** `project_day_to_day_intents` (finishes categories E,F,G,H,I,J,K,N,O + partials), the deferred mass-crawl, the office/international pilots.

---

## 1. Goal & non-goal

**Goal:** if a grad-student need is answerable from njit.edu, the bot has it — comprehensively, but **curated to grad-relevant content**, grounded (verbatim spans), with volatile/high-stakes facts handled safely.

**Non-goal:** a literal whole-site dump. njit.edu is thousands of pages (news, marketing, athletics, undergrad, every lab); ingesting all of it **degrades retrieval** (noise crowds top-K), mixes in stale high-stakes facts, and reverses the deliberate mass-crawl deferral. The true long tail stays on the **live Brave fallback** (on demand, zero KB bloat).

## 2. Scope — the grad-relevant njit.edu trees

Each is crawled *within its own site tree* (bounded BFS from a seed, same-host, depth-limited), not the whole domain:

| Section | Seed tree | Categories it fills | Stakes |
|---|---|---|---|
| **Bursar** | `njit.edu/bursar/*` | G billing | HIGH ($/dates) |
| **Registrar** | `njit.edu/registrar/*` | H registration, K transfer, finish I | mixed |
| **Graduate Studies** | grad-studies site | F funding, J advising, N thesis, finish O | mixed |
| **OGI (international)** | already 43 items — verify/extend | D, L (done) | HIGH |
| **Financial Aid** | `njit.edu/financialaid/*` | F funding | HIGH (deadlines) |
| **Career Development** | career site | career | LOW |
| **Counseling (C-CAPS)** | counseling site | wellness | LOW |
| **OARS (accessibility)** | accessibility site | accommodations | LOW |
| **IST / tech** | IST site | Wi-Fi/Canvas/Pipeline/VPN | LOW |
| **Dean of Students** | DoS site | conduct/advocacy | mixed |
| **Campus logistics** | housing · parking · dining · ID | logistics | LOW |
| **Academic calendar** | the official calendar | deadlines across cats | HIGH (dates) |

Exact seed URLs go in a **URL registry** (§4). Most offices already have an org node + a 1-item shell to fill.

## 3. The safety policy (the heart of this)

Three buckets, decided per extracted fact:

1. **Low-stakes** (office hours/location/contact, how-to procedures, service descriptions, where-to-find) → ingest **live** (`is_active=1`). No sign-off.
2. **Volatile specifics** (exact tuition $, fee amounts, term deadlines, USCIS fees) → **never ingested as a bare value.** Rewritten as *"rule + link to the live page"* (e.g. "Tuition is billed per credit; current rates: njit.edu/bursar/tuition-and-fee-schedule"). The bot can never assert a stale number.
3. **High-stakes static facts** (immigration rules, billing process w/ consequences, academic standing/dismissal, health-insurance waiver mechanics, test-score requirements) → ingested **staged**: `is_active=0` + `metadata.stakes='high'` + written to a generated **review list** (`docs/review/<section>-high-stakes.md`). They go live **only** when Mohammad approves (`--approve <section>` flips `is_active=1`). Until then they're invisible to students.

**Classification** = a deterministic keyword/section ruleset (immigration·visa·CPT·OPT·SEVIS·I-20 / tuition·fee·bill·payment·refund·$ / deadline·due·date / GRE·TOEFL·score / probation·dismiss·conduct / insurance·waiver), reviewable + tunable. Default unknown → treat as high-stakes (safe).

## 4. Reusable pipeline (extends what exists)

Reuse: `grounded_extract` (verbatim spans, drops non-present), `upsert_doc_items` (section-aware gated ingest), the `ingest_office_docs.py` folder→org pattern, `explore.http_fetch` (project UA), the `_crawl_stage` fetch/clean.

New, generalized into `scripts/crawl_njit_section.py` (one entry, any section):
```
URL registry (per section: seeds + volatile-link-only URLs)
   → fetch (bounded BFS in-tree, http_fetch, project UA)
   → clean + grounded_extract (verbatim spans only; non-present dropped)
   → CLASSIFY each span: low / volatile / high-stakes
       • volatile → rewrite as "rule + live link"
       • high    → metadata.stakes='high', is_active=0, append to review list
       • low     → is_active=1
   → write bot/data/sources/njit/<section>/*.md (front-matter: org, stakes, source_url)
   → gated ingest under the section's org (backup → dry-run → --commit)
   → embed_all.py
   → per-section gold test test_<section>_gold.py (intents @ rank<=2)
```
A `--approve <section>` step flips the staged high-stakes items live after Mohammad's review.

## 5. Dashboard

Add a **Jobs** control: "Refresh NJIT content → [section]" runs `crawl_njit_section.py` as a supervised job (reuses the existing Jobs runner), and a **High-stakes review** panel listing `is_active=0, stakes='high'` items with an Approve button (flips live). So re-running/refresh and sign-off are one-click.

## 6. Per-section deliverable (each is a gated checkpoint)
1. Ingested low-stakes content **live**.
2. Volatile facts → live-link phrasing.
3. High-stakes → **staged + a review list for Mohammad**.
4. `test_<section>_gold.py` green + full suite green.
5. `eval.sh` **before/after** numbers (must not regress overall retrieval).
6. A short checkpoint report → Mohammad approves high-stakes → deploy (bot restart).

## 7. Build order
Reusable pipeline + senior review → **Bursar (G)** → **Registrar (H/K/I)** → **IST + Counseling + OARS + Career + DoS** (low-stakes, fast) → **Grad Studies (F/J/N/O)** → **Financial Aid (F)** → **Academic calendar** → **finish Admissions E** → campus logistics.

## 8. Risks / for review
- **R1 — retrieval regression** from volume. Gate: `eval.sh` before/after per section; the answerability/rerank layer already guards precision. If a section drops overall accuracy, trim it.
- **R2 — classification misses a high-stakes fact** → it goes live wrong. Mitigation: unknown→high default; the immigration/billing/funding **heads-up** still fires; volatile-link routing means no bare numbers. Review the ruleset.
- **R3 — staging mechanism** (`is_active=0` + approve). Confirm staged items are truly invisible to retrieval (the answer corpus already filters `is_active=1`) and that approve is auditable.
- **R4 — crawl politeness / JS pages.** Bounded in-tree BFS, rate-limited, project UA. Some office pages may be JS-rendered → fall back to the live link (don't fabricate).
- **R5 — volatile rewrite quality.** "Rule + link" must read well and always carry the real URL. Review the rewrite step.
- **R6 — provenance.** Every item carries `source_url`; re-runs reconcile by natural_key (idempotent), never duplicate.

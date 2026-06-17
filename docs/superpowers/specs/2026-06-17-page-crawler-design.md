# NJIT Page Crawler → Grounded KB Content — Design

**Date:** 2026-06-17
**Status:** Senior-reviewed; RE-SCOPED to Sub-project 2 (deferred). Architecture changed to KB-first + live-search fallback: this batch crawler becomes the KB-population optimization AFTER the grounded-core + live-fallback ship (see 2026-06-17-live-search-fallback-design.md). Senior review reshaped it to extractive/verbatim + sentence-grounding (folded into the new spec's shared core).
**Relates to:** `project_day_to_day_intents` (eliminates the manual paste-per-page tedium for the
remaining categories), `project_hybrid_ingestion` (reuses the Phase 1b grounded crawl/extract),
`2026-06-17-admissions-pilot-design.md` (the validation target), `2026-06-11-hybrid-knowledge-ingestion.md`.

## Problem & premise

Hand-pasting NJIT page text per topic (offices, OGI, admissions) is tedious and doesn't scale to
the remaining categories. The content is **public, server-rendered NJIT pages** — nothing hidden
or behind a login. So we crawl it. The risk with crawl+LLM is hallucinated facts; the project
already solved that for faculty sites with **span-grounding** (every extracted fact carries a
verbatim quote that must appear literally on the page, else it's discarded), which makes the local
8B safe. This subsystem **adapts that proven pipeline from faculty sites to NJIT office/policy
pages**, producing the same "overview + route" KB docs we've been hand-writing.

## Approach (decided)

- **Reuse:** `web_crawler.crawl_site` (bounded, same-domain, robots/politeness, injected fetch),
  `explore.http_fetch`, the `raw_pages` cache, the grounding check from `web_extract`, the
  section chunker + `upsert_doc_items` ingest.
- **New:** a curated URL registry, a policy-shaped grounded extractor, an overview generator, and
  a runner — mirroring `scripts/ingest_faculty.py`.
- **Strategy A (grounded extract → grounded overview):** page → span-verified policy facts →
  overview doc generated from ONLY verified facts, ending with "confirm with <office>." Chosen over
  grounded free-form summarization (fuzzier grounding) and pure extractive excerpts (less readable;
  kept as the fallback if generation proves shaky).

## Architecture & data flow

```
curated SEEDS {category: [NJIT URLs]}
  → crawl_site(seed, fetch=http_fetch, max_depth=1, budget)   [reuse] → raw_pages cache
  → policy_extract(page_text, source_url, call_llm) -> [GroundedFact]  [new; reuses grounding]
  → policy_overview(category, office, facts, call_llm) -> markdown doc  [new]
  → write bot/data/sources/<category>/<slug>.md (front-matter title+source_url; source='crawler')
  → gated ingest (ingest_office_docs folder→org map; section chunker; embed; prune)  [reuse]
  → the category's gold gate                                              [reuse]
```

## Components

### 1. `v2/core/ingestion/page_registry.py`
The only human-curated piece: `SEEDS: dict[str, list[str]]` (category → seed URLs) and a
`CATEGORY_ORG: dict[str, str]` (category → org slug, e.g. `admissions → graduate-admissions`).
Curated, not a blind spider; `crawl_site` does a shallow (depth-1) same-domain expansion to catch
obvious sub-pages, relevance-gated, with a page budget.

### 2. `v2/core/ingestion/policy_extract.py`
`extract_policy(page_text, source_url, call_llm) -> list[GroundedFact]`. The LLM returns key
student-facing facts (eligibility, deadlines, requirements, steps, contacts) each with a
**verbatim evidence quote**. We keep a fact ONLY if (a) the quote appears **literally** on the
page and (b) the fact's significant words appear in the quote (the `_value_supported` check
imported/shared from `web_extract`). Long pages are chunked into windows (decompose, never
truncate); facts are grounded against the full page text and de-duplicated. The LLM call is
**injected** (`call_llm(system, user) -> str`) so unit tests run offline.

### 3. `v2/core/ingestion/policy_overview.py`
`build_doc(category, office, source_url, facts) -> str` assembles the front-matter + an
"overview + route" markdown body from the verified facts, via a grounded generation step
(`generate(facts, call_llm)`, instructed to invent nothing and end with "confirm with <office>").
Generation input = verified facts only, so the output is a faithful compression, not outside
knowledge. Pure assembly is unit-tested; the LLM call is injected.

### 4. `scripts/crawl_pages.py` (runner, gated)
For each category (or `--category X`): crawl each seed, extract+ground, generate docs, and write
them to `bot/data/sources/<category>/`. Dry-run by default writes the candidate `.md` files for
inspection; `--commit` runs the gated ingest (`hardened_backup`, `ingest_office_docs --commit`
path, `embed_all`, prune). `source='crawler'`.

## Grounding = the trust core (safety)

Every fact must carry a verbatim quote that is literally present on the page; the overview is
generated from only surviving facts and asserts nothing else. So even fully-unreviewed crawl
output cannot state a fact that isn't on the NJIT page. Conservative generation ("confirm with
<office>") + the immigration/billing/funding heads-up (already live) reinforce safety for
high-stakes pages.

## Error handling

| Condition | Behavior |
|---|---|
| Fetch fails / non-HTML / robots-disallow | page skipped, logged, never fatal |
| LLM returns no/invalid facts for a page | that page produces **no doc** (never ship an empty/ungrounded doc) |
| All writes | gated: `hardened_backup`, dry-run default, `--commit`; `source='crawler'` so re-crawl reconcile updates them and never clobbers hand-authored `dashboard` docs |
| Re-run | idempotent per doc slug (existing `upsert_doc_items` retire+reinsert) |

## Testing & acceptance gate

**Unit (pure, offline — injected fetch + injected LLM; like the existing crawler tests):**
- grounding drops a fact whose quote is **not** literally on the page (the safety property).
- a fact whose value isn't supported by its quote is dropped.
- registry/crawl policy: same-domain, depth, relevance, budget.
- `policy_overview` assembles front-matter + body from facts; ends with the office route line.

**Acceptance gate = the existing category gold gate, run in isolation.** To compare crawl quality
against hand-written *without* mixing the two (the hand-written admissions docs already live in the
DB and in `sources/admissions/`), the validation crawls Admissions to a **scratch dir**, ingests
**only the crawled docs into a scratch database** (`create_all(<tmp>.db)` + the org rows + crawled
docs + `embed_all` against that scratch DB), and asserts the crawled docs satisfy the
`admissions_gold.py` gold-token map at rank ≤2 (the same tokens `test_admissions_gold` checks).
The live DB and the hand-written `dashboard` docs are untouched. This is the objective "crawl
quality ≥ hand-written" proof. (Once proven on Admissions, new categories crawl straight into the
live DB as `source='crawler'`.)

**Safety assertion (integration):** an LLM stub that returns a fact with a quote NOT on the page →
that fact is dropped and never reaches a doc.

**Acceptance bar:** crawler on Admissions → docs → **18/18 admissions gate green**, grounding-drop
test green, no regressions on other gates. Then the crawler is trusted to scale to new categories
(Registration, Academic, Billing, and back-fill).

## Out of scope (separate efforts)
- Broad NJIT-wide spidering (we crawl a curated registry + depth-1 expansion).
- Non-HTML (PDF) parsing.
- Auto-scheduling re-crawls (the Jobs control plane can run `crawl_pages.py` later).
- Replacing the hand-written `dashboard` docs already shipped — the crawler adds/refreshes
  `crawler`-sourced docs; we decide per category whether to migrate.

## Files
- Create `v2/core/ingestion/page_registry.py`, `policy_extract.py`, `policy_overview.py`.
- Create `scripts/crawl_pages.py` (runner).
- Create `v2/tests/test_policy_extract.py`, `v2/tests/test_policy_overview.py`,
  `v2/tests/test_crawl_admissions_gate.py` (the validation).
- Reuse `web_crawler.py`, `web_extract.py` (grounding), `explore.http_fetch`, `section_chunker`,
  `gsa_docs.upsert_doc_items`, `ingest_office_docs` folder→org map.

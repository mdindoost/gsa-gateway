# Build A — NJIT Catalog Crawl (catalog.njit.edu) — Design

> Status: DESIGN (awaiting expert reviews + owner sign-off per the EXPERT-REVIEW HARD GATE).
> Date: 2026-06-29. Author: Claude (Opus 4.8), with Mohammad.
> Companion of: Build B (www.njit.edu-deep reconcile) — a **separate** spec, deferred.

## 1. Why (trigger)

On 2026-06-29 a Data Science PhD student got **confidently wrong** answers about course counts
and qualifying-exam timing: the bot served Mathematical Sciences / Computer Science / Chemical &
Materials Engineering rules as if they were the DS rules, and even overrode the student's own
correct pasted text.

Verified root cause: the authoritative page —
`catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd/` — and nearly all of
`catalog.njit.edu` is **not in the corpus**. Live DB at design time:

- `catalog.njit.edu`: **19 rows total, 3 distinct URLs**, all incidental (9 `created_by='crawler'`
  from the people crawler, 10 `created_by='dashboard'` manual). **Zero `created_by='college_crawl'`.**
- `catalog.njit.edu` is **not** in `entry_points.PROSE_ENTRY_POINTS`.
- `college_crawl` is host+path scoped to college **subdomains**; it never crosses to the
  `catalog.njit.edu` host.

The graduate + undergraduate catalog is the authoritative home of **every** program's requirements.
It was never seeded. Build A seeds it.

This gap was found in teacher-eval phase 1 and parked. Build A stops parking it.

## 2. Scope

**In scope (Build A):** the entire current/canonical `catalog.njit.edu` tree — graduate +
undergraduate + about-university — as `knowledge_items` prose.

**Out of scope (deferred, flagged — not dropped):**

- **Build B** — reconcile the ~67 `www.njit.edu`-deep phase-1 seed cites against the live office
  crawlers, then crawl only what is genuinely uncovered. Separate spec, designed + gated after A
  ships (A absorbs the `/academics/degree/*` overlap, shrinking B).
- **Companion abstain/program-scoping defect** — even with the gap closed, the bot should ABSTAIN
  ("I don't have that program's page") rather than serve a sibling program's rules. That is a
  retrieval/serving defect, tracked separately ([[project_open_items]]). Build A is **data-bringing
  only** and does not touch serving.

## 3. Ground-truth facts (verified 2026-06-29, not assumed)

- **CourseLeaf catalog with a clean `sitemap.xml`** = **446 `<loc>` URLs** (234 undergraduate,
  196 graduate, 12 about-university, + `programs`, `azindex`, `pdf` index pages). Smaller than a
  single existing college subdomain (`college_crawl` holds 3,337 rows today).
- **Year-versioning is solved by the source.** `robots.txt` **Disallows `/archive/`** (past-year
  trees) and the sitemap enumerates exactly the current canonical tree. "Crawl current year only"
  == "crawl the sitemap, honor robots." No manual year-pinning.
- **Program pages are server-rendered.** The DS-PhD trigger page returns the full requirements as
  plain readable text (total credits, "within two years (24 months)… end of the third semester"
  qualifier rules, dissertation timeline, DS 675/644 + MATH 644 course list). A plain HTTP fetch
  captures the authoritative content. No JS/iframe rendering needed.
- **Catalog top-level taxonomy → existing orgs** (the hybrid org map the owner approved):

  | catalog 2nd-level segment | org slug |
  |---|---|
  | `computing-sciences` | `ywcc` |
  | `science-liberal-arts` | `csla` |
  | `newark-college-engineering` | `nce` |
  | `architecture-design` | `hcad` |
  | `management` | `mtsm` |
  | `honors-college` | `honors` |
  | everything else (`academic-policies-procedures`, `admissions-financial-support`, `admissions-financial-aid`, `campus-life-student-services`, `graduate-programs`, `continuing-professional-education`, `special-programs`, `contact-department`, `about-university/*`, `programs`, `azindex`, `pdf`) | `njit` (root) |

  All six target orgs already exist (people + prose layers). `njit` root exists. `ensure_org`
  early-returns for all → no new org creation, no tier changes.

- **Content container verified (CourseLeaf).** The DS-PhD page has `<div id="content"
  role="main">`, and the left-nav program tree sits **outside** it. `eos_crawl._main_region`
  matches `div[role=main]` first, so it scopes to the content column and the 446-page nav tree is
  excluded (it is NOT the whole-page fallback case). Residual minor chrome (breadcrumb / page
  header inside `#content`) is spot-checked in the dev dry-run.
- **People-path skip is exactly one page.** Grep of all 446 `<loc>` URLs against the eight
  `_PEOPLE_SEGMENTS`: the only match is `/about-university/directory/faculty/` — a pure faculty
  roster, correctly skipped (the KG owns faculty). No real prose page is dropped.
- **No news/event segments** in the catalog → `classify_type` returns `policy` for every page; no
  page is subjected to the retriever's recency decay.

## 4. Design

### 4.1 Crawl method — sitemap-driven (decided)

Drive the frontier from `sitemap.xml`, not DFS. Rationale: automatic year-pinning, exact 446-page
coverage with no truncation (DFS `budget=400 < 446`), deterministic/repeatable, and zero admin/
course-admin/directory noise. The catalog publishes its own canonical index; crawl that index.

### 4.2 Architecture — maximal reuse of `college_crawl`

Reused **unchanged**: `extract_prose`, `extract_dates`, `classify_type`, `is_people_path`,
`ingest_college`, `ingest_pdf_pages`, `_canon`, `web_crawler.make_fetcher` / `make_bytes_fetcher`,
`scripts/_area_tag_migrate.hardened_backup`.

New, isolated pieces (all in a new module `v2/core/ingestion/catalog_crawl.py` + a runner
`scripts/crawl_catalog.py`):

1. **`catalog_seed_urls(fetch_bytes, sitemap_url="https://catalog.njit.edu/sitemap.xml") -> list[str]`**
   **(B1)** The sitemap is `application/xml`; `make_fetcher` is HTML-only and would return `None`
   (`web_crawler.py:289`) → empty frontier → silent no-op. So this takes **`fetch_bytes`**
   (`make_bytes_fetcher`, returns bytes regardless of content-type) and decodes the XML. Parse
   `<loc>` values, drop empties, drop any URL whose path starts with a robots-disallowed prefix
   (`/archive/` is the year-relevant one; honor the robots disallow list defensively).
   **(S6 — normalize once)** Each kept URL is normalized exactly here via the same pipeline
   `college_crawl` uses — `_canon(normalize_url(u, u))` (scheme→https; the spec also **strips the
   trailing slash** — one chosen direction, applied uniformly — so a year-to-year slash flip doesn't
   churn; CourseLeaf 301-redirects both ways so the stored provenance link still resolves). Returns the canonical current frontier,
   deduped. **Invariant: a catalog URL is normalized once, in `catalog_seed_urls`, and flows
   unchanged into both storage (`source_url`/`natural_key`) and retirement — nothing re-normalizes
   downstream.**

2. **`CATALOG_ORG_MAP: dict[str, tuple[slug, name, parent_slug]]`** keyed by catalog 2nd-level
   segment, plus a default → `njit` root, encoding §3's table. Pure resolver:
   **`org_for(url) -> tuple[slug, name, parent_slug]`** — split the path, read the segment after
   `graduate`/`undergraduate` (or `about-university` etc.), map; unknown → `njit`.

3. **`crawl_catalog(fetch, fetch_bytes, urls=None) -> CatalogResult`** — orchestrator:
   - `urls = urls or catalog_seed_urls(fetch_bytes)` — the **sitemap frontier** (HTML pages fetched
     with the normal `fetch`; the sitemap itself with `fetch_bytes`, per B1).
   - Group URLs by `org_for(url)` slug.
   - For each org group: fetch each URL with `fetch`, run `extract_prose` (verbatim), apply
     `is_people_path` skip (drops the lone `/about-university/directory/faculty/` roster — verified,
     §3), content-hash dedup, stash raw HTML for `extract_dates`. Build a `college_crawl.EntryResult`
     per org group, **ingest that group, then release its `html_by_url`** before the next group
     (N1 — caps peak memory; do not hold all 446 pages' raw HTML at once).
   - **`CatalogResult` carries the SITEMAP frontier set (`urls`), NOT the kept set** — the
     retirement pass (§4.3) compares against the authoritative frontier so a page that transiently
     failed one fetch (skipped this run) is never wrongly retired (S2).

4. **Ingest seam (B3 — bigger than "1 line").** `ingest_college` and `ingest_pdf_pages` each
   reference `PROSE_SOURCE` in **three** load-bearing places: the meta `"source"`, the existence-
   check `SELECT … AND created_by=?`, and the `INSERT … created_by`. Add a
   `created_by: str = PROSE_SOURCE` parameter to **both** functions and replace **all three**
   references in each with the param (the SELECT binding especially — if it still matches the old
   constant while the INSERT writes the new one, the existence check never finds the row and every
   run inserts duplicates). `catalog_crawl` passes `created_by='catalog_crawl'`. Default unchanged →
   every existing `college_crawl` caller is byte-for-byte unaffected (additive). **(N3)** `meta["source"]`
   therefore also becomes the param, so catalog rows carry `metadata.source='catalog_crawl'`.
   - `type` is `classify_type(url)` → `policy` for every catalog page (verified: no /news //event).
   - Idempotent on `(org_id, natural_key=source_url, created_by='catalog_crawl')`.

5. **PDFs** — `.pdf` links found in kept prose pages' `files` lists go through
   `ingest_pdf_pages(..., created_by='catalog_crawl')`: extract-or-skip-and-flag, the existing PDF
   policy. PDF rows have `type='pdf'` and a `natural_key` = the asset URL (**never** a sitemap
   `<loc>`), which is why the retirement pass must exclude `type='pdf'` (§4.3, B2).

### 4.3 Reconcile / recurrence (yearly refresh)

Re-run = re-fetch sitemap + re-ingest: unchanged rows skipped (content-hash), changed rows
version-bumped — same mechanism as `college_crawl`. **Plus a catalog-scoped retirement pass**
(`reconcile_catalog(conn, sitemap_urls)`):

- Compares the live active catalog rows' `natural_key` against the **sitemap frontier set**
  (`sitemap_urls`, the SAME list that drove the crawl — passed in, never re-fetched; S2). Any
  `is_active=1` row with `created_by='catalog_crawl'` **AND `type='policy'`** whose `natural_key`
  is not in `sitemap_urls` is set `is_active=0`.
- **(B2) Excludes `type='pdf'` rows.** PDF `natural_key`s are asset URLs that are never sitemap
  `<loc>` entries; including them would retire every PDF on every run (insert → retire → re-insert
  thrash). PDF freshness is handled by content-hash idempotency on re-ingest, not by this pass.
- This makes the academic-year rollover clean — programs the catalog drops retire instead of
  lingering stale. Scoped to `created_by='catalog_crawl'` → touches nothing else (isolation invariant).

**Guards (S1 — a partial parse must not mass-retire):**
- If `catalog_seed_urls` returns empty (sitemap fetch failure) → skip retirement entirely.
- **Floor:** skip retirement unless `len(sitemap_urls)` is at least `max(300, 0.8 × prior_active_
  policy_count)` (a truncated XML read yielding e.g. 50 of 446 `<loc>` is non-empty but must NOT
  trigger retirement of the other ~396). The runner logs loudly when the floor blocks a pass.
  **`prior_active_policy_count` is scoped to `created_by='catalog_crawl' AND type='policy'`** — NOT
  all policy rows. If computed over the whole corpus (thousands of `college_crawl` policy rows),
  `0.8×` would be unsatisfiable and retirement would never run, letting stale prior-year program
  requirements linger across the rollover (the opposite failure). **The count is sampled BEFORE
  this run's ingest** (or excludes the current run's just-inserted/version-bumped rows) so the floor
  measures the prior state, not a number this run inflated.
- `--limit N` (dev subset) **forces `--no-reconcile`** (S5) — a partial frontier never retires.

### 4.4 Isolation & invariants honored

- `created_by='catalog_crawl'` — distinct source; reconcile is source-scoped; never cross-wipes
  `college_crawl` (subdomain prose), `crawler` (people), `scholar`, or `dashboard` (manual GSA).
- Catalog program pages and college-subdomain prose **coexist** (different hosts, different
  `source_url`/`natural_key`, complementary content — catalog = authoritative requirements,
  subdomain = overview/marketing). Content-hash dedup only collapses byte-identical content within
  the same org+source.
- **Crawl = data-bringing only.** Mechanical clean + verbatim text. No serving/gating/decline.
- **Never insert `search_text`** (generated). Embeddings via `embed_all.py` + `embed_chunks.py`.
- **Org mapping is curatorial/provenance, not a retrieval-ranking lever.** General RAG runs with
  `org_id=None` and applies no org filter (`retriever.py`), so the hybrid map does not change
  ranking today — its value is provenance (`org_path` shown to the LLM), reconcile scoping, and a
  possible future org-scoped route. The spec does not claim retrieval gains from it (RAG N4).
- Gated live write: dev-copy → dry-run → `--commit` with `hardened_backup` → `embed_all`.

## 5. Components & interfaces (isolation map)

- `v2/core/ingestion/catalog_crawl.py` — NEW. `catalog_seed_urls`, `CATALOG_ORG_MAP` + `org_for`,
  `crawl_catalog`, `reconcile_catalog`, `CatalogResult`. Depends on `college_crawl`, `web_crawler`,
  `eos_crawl` (via `college_crawl` re-exports), `orgs`. Pure functions where possible (`org_for`,
  sitemap parse) for unit testing without network.
- `v2/core/ingestion/college_crawl.py` — ONE additive change: `created_by` param on
  `ingest_college` + `ingest_pdf_pages` (default `PROSE_SOURCE`, so all existing callers unchanged).
- `scripts/crawl_catalog.py` — NEW gated runner. Flags: `--db`, `--commit`, `--embed` (runs
  `embed_all.py` then `embed_chunks.py`), `--delay` (politeness between fetches), `--no-reconcile`
  (skip the retirement pass), `--limit N` (dev subset — first N sitemap URLs; **forces
  `--no-reconcile`**, S5). No `--budget`: the frontier IS the sitemap, so there is no DFS page
  budget. Sitemap fetched via `make_bytes_fetcher`; HTML pages via `make_fetcher`. Mirrors
  `scripts/crawl_college.py` structure (hardened_backup, dry-run default, dev-copy workflow).
- `entry_points.py` — NOT modified. The catalog is sitemap-driven (one host fanning to many orgs),
  which does not fit the one-seed-one-org `PROSE_ENTRY_POINTS` shape; keeping it in its own module
  is cleaner than overloading that registry.

## 6. Error handling

- Sitemap fetched with `fetch_bytes` (not `make_fetcher`, which rejects XML — B1). Fetch fails /
  empty / parses to too few URLs → `catalog_seed_urls` returns `[]` or a short list; runner prints a
  clear error and the retirement floor (§4.3) **blocks the retirement pass** (no mass-retire on a
  glitch); ingest of whatever was fetched still proceeds non-destructively.
- A page fetch returns no HTML → skipped + flagged (`EntryResult.skipped`), never stored.
- `extract_prose` returns `None` (no readable content) → skipped + flagged.
- A PDF that is empty/image-heavy/invalid → manifest skip, no row (existing `ingest_pdf_pages`).
- `org_for` on an unexpected path → defaults to `njit` root (never crashes, never drops a page).

## 7. Testing (TDD)

Unit (no network, injected `fetch`/`fetch_bytes`):
1. `catalog_seed_urls` decodes XML from `fetch_bytes`, parses `<loc>`, drops empties, **excludes
   `/archive/`** and other robots-disallowed prefixes; normalizes once (`_canon`+trailing-slash);
   returns deduped canonical set. (B1 + S6)
2. `org_for` maps each of the 6 college segments correctly; unknown/university-wide → `njit`;
   handles `/graduate/…`, `/undergraduate/…`, `/about-university/…`, and bare `/programs/`.
3. `crawl_catalog` groups by org; applies `is_people_path` skip (`/directory/faculty/` dropped);
   content-hash dedups; populates then **releases** `html_by_url` per group (N1); `CatalogResult`
   carries the **sitemap set**, not the kept set (S2).
4. Ingest writes `created_by='catalog_crawl'`, `metadata.source='catalog_crawl'` (N3),
   `type='policy'`, correct `org_id`; **idempotent re-run under the non-default `created_by`**
   (no duplicate insert — guards B3's SELECT-binding bug); changed content version-bumps.
5. `reconcile_catalog`: retires a `type='policy'` row whose URL left the sitemap; **does NOT retire
   `type='pdf'` rows** (B2); **empty set → retires nothing**, and a **below-floor set (e.g. 50 of
   446) → retires nothing** (S1 guard).
6. PDF skip-flag path (reuse existing `ingest_pdf_pages` tests as the pattern); a PDF row survives a
   reconcile pass (B2 regression).
7. `created_by` default unchanged → existing `college_crawl` callers/tests still pass (regression).

Integration (gated, manual):
- Dev-copy dry-run + `--commit` on `/tmp/dev.db`; inspect row counts per org; spot-check 2–3
  extracted page texts for nav/chrome pollution (S3); `verify_kg`.
- Live `--commit` → **`embed_all.py` AND `embed_chunks.py`** (RAG blocker — the live deep-fallback
  + answer-gate depend on chunk vectors; whole-doc embed truncates at 2000 chars).
- **No-regression gate (RAG S2), run pre vs post and required GO before keeping live:**
  (a) the office-routing gold set (`test_office_routing_gold`) — no new dilution regression;
  (b) `scripts/eval.sh` pre/post — coverage/accuracy not worse;
  (c) an **underspecified-sibling probe set** — "data science qualifying exam" (no level word),
  "data science courses", CS-PhD vs DS-PhD vs Math-DS-MS — record the wrong-program rate
  (quantified, not asserted). Acceptance checks run **with the answer-gate ON**.
- Acceptance: the DS-PhD trigger questions answer from the authoritative page; a well-formed sibling
  query (CS PhD, Math DS-MS) returns ITS own page.

## 8. Gated rollout

```
cp gsa_gateway.db /tmp/dev.db
python scripts/crawl_catalog.py --db /tmp/dev.db            # dry-run
python scripts/crawl_catalog.py --db /tmp/dev.db --commit   # dev write, inspect + verify_kg
python scripts/crawl_catalog.py --commit --embed            # live (hardened_backup; --embed runs
                                                            #   BOTH embed_all.py AND embed_chunks.py)
bash scripts/ask.sh "data science phd qualifying exam requirements"   # spot-check (gate ON)
```
`--embed` runs `v2/scripts/embed_all.py` **then** `v2/scripts/embed_chunks.py` (both under
`v2/scripts/`, invoked by full path like `crawl_college.py` does; resumable — only new items). The
chunk pass is mandatory — the live deep-fallback + answer-gate read chunk vectors, and whole-doc
embed truncates long catalog pages at 2000 chars. DB-only change → no bot restart.

## 9. Goals checklist (shipped / deferred — fill at PR)

- [ ] Entire current `catalog.njit.edu` tree ingested as `catalog_crawl` prose (the ~446 sitemap URLs).
- [ ] Hybrid org mapping (program→college, university-wide→njit root).
- [ ] Year-pinning via sitemap + robots `/archive/` exclusion (no archived years ingested).
- [ ] PDF content handled per existing skip+flag policy (tail flagged, not silently dropped).
- [ ] Source isolation: `created_by='catalog_crawl'`; reconcile never cross-wipes other sources.
- [ ] Repeatable refresh + retirement pass for dropped pages (yearly rollover), with the empty +
      below-floor guards (S1) so a partial sitemap never mass-retires.
- [ ] Chunk-embedded (`embed_chunks.py`) so the live deep-fallback + answer-gate see catalog content.
- [ ] No-regression gate GREEN (office-routing gold + `eval.sh` pre/post + sibling probe).
- [ ] DS-PhD trigger query answered from the authoritative page (acceptance check, gate ON).
- [ ] **DEFERRED & FLAGGED — Build B** (www-deep reconcile): separate spec.
- [ ] **DEFERRED & FLAGGED — companion abstain/program-scoping defect** (separate item). ⚠️ NOTE
      the residual this build introduces: seeding 3 near-duplicate "data science" programs
      (computing-sciences/data-science-phd→ywcc, computer-science/data-science-ms→ywcc,
      mathematical-sciences/data-science-ms→csla) slightly **broadens** the wrong-program surface
      for *underspecified* queries until the abstain fix ships. The exact trigger (page-absent →
      unrelated-dept) is fixed; vague "data science qualifying exam" with no level may still serve a
      confident sibling answer. Measured by the sibling probe set above; not worse than today for
      the trigger itself.

## 10. Risks

- **Sitemap drift** — if NJIT changes the sitemap location/format, `catalog_seed_urls` returns
  empty / short; guarded by the empty + below-floor checks (§4.3, S1) → no mass-retire, clear error.
- **CourseLeaf "shared" requirement blocks** — some program pages embed shared course tables; these
  are server-rendered text (verified on the DS-PhD page) so `extract_prose` captures them verbatim.
- **bm25 title quality (RAG N5)** — `search_text = title || ' ' || content`, and the keyword leg is
  load-bearing for past-truncation requirements text. Verify in the dev dry-run that CourseLeaf page
  titles are program-specific ("Data Science, PhD") not a generic site title; if generic, set a
  mechanical title from the `<h1>`/breadcrumb leaf. (Spot-check item in §7.)
- **Politeness** — 446 fetches with a default delay; single sequential pass, well within courtesy.
- **Conservative-by-design: a real >20% one-year catalog shrink blocks retirement** — the S1 floor
  treats a sudden large drop as a likely partial-fetch and skips retirement (stale rows linger,
  logged loudly) rather than mass-retire. This is the correct trade per the never-mass-retire hard
  line; if NJIT ever genuinely cuts >20% of programs, a maintainer re-runs with the floor lowered.

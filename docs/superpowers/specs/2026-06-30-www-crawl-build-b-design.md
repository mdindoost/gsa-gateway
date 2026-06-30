# Build B — Complete www.njit.edu mirror (`www_crawl`) — Design

> Status: DESIGN (awaiting expert reviews + owner sign-off per the EXPERT-REVIEW HARD GATE).
> Date: 2026-06-30. Author: Claude (Opus 4.8), with Mohammad.
> **This is a LEAN DELTA-SPEC on top of Build A** (`2026-06-29-catalog-crawl-build-a-design.md`).
> It reuses Build A's proven sitemap-driven engine and only states what differs. Read Build A
> first; everything there (extraction reuse, S1 floor guards, S6 normalize-once, B1 bytes-fetcher,
> data-bringing-only hard line, gated rollout) applies here unchanged unless noted.

## 1. Goal (owner, 2026-06-30)

> "Whatever is on the NJIT site, we should have it in the DB — captured **once**, and every recrawl
> after that is **complete by construction**." Build A did this for `catalog.njit.edu`. Build B does
> it for the one remaining uncovered host, `www.njit.edu`.

The other NJIT hosts are already complete: catalog (Build A), college/dept subdomains
(`college_crawl`), people (`explore.py`). Build B owns `www.njit.edu` prose.

## 2. Why the current state is incomplete (DB-verified 2026-06-30, not assumed)

The office crawlers (`bursar_crawl`, `registrar_crawl`, … — copies of `eos_crawl`) used a **DFS with a
budget (300) + depth (4) cap**. That cap silently dropped pages: they captured `node/<id>` and `.php`
aliases but **missed the clean-URL deep pages students actually cite**. Measured against each subsite's
**own `sitemap.xml`**:

| office subsite | sitemap URLs | in corpus | gap (notable missing cited page) |
|---|---|---|---|
| registrar | 124 | 71 | `/registrar/transcript`, `/calendars`, `/graduation`, `/diploma-day` |
| global (OGI) | 100 | 94 | — |
| financialaid | 96 | 91 | `/matriculated-graduate-students`, `/dates-and-deadlines`, loans |
| careerservices | 94 | 66 | `/find-your-career-advisor`, `/career-fairs`, `/co-op-internship` |
| environmentalsafety (eos) | 89 | 58 | — |
| graduatestudies | 68 | 50 | `/content/new-phd-credit-requirements`, `/contact.php` |
| bursar | 52 | 22 | **`/payment-information` (cited 10×)**, `/tuition-and-fee-schedule`, `/faqs` |
| dos | 49 | 21 | `/reporting`, `/standards-student-conduct` |
| parking | 39 | 36 | `/daily-parking-options` |
| mailroom | 36 | 24 | — |
| counseling / sustainability | 16 / 16 | 16 / 10 | cited counseling pages still absent by URL |

**Verified fact:** of the 67 `www.njit.edu` pages the teacher-eval Phase-1 oracle cited, **0 are present
by exact URL** — even in "covered" offices. (Content topics are partly present under node/`.php` aliases,
but the specific authoritative pages are not.)

And the **genuinely uncovered** service subsites (near-zero in corpus), each with its own sitemap:

| subsite | sitemap URLs | in corpus |
|---|---|---|
| provost | 83 | 0 |
| reslife | 67 | 0 |
| studyabroad | 58 | 0 |
| finance | 52 | 1 |
| eop | 41 | 0 |
| publicsafety | 38 | 0 |
| policies | 33 | 1 |
| president | 33 | 1 |
| persistence | 14 | 0 |
| accessibility | 12 | 1 |
| writingcenter | 10 | 0 |
| studentinvolvement | 4 | 0 |

Plus the **main `www.njit.edu/sitemap.xml`** (~499 URLs) = `/academics/degree` (209) + `/academics/major`
(68) + marketing/landing pages. These program landing pages are **complementary** to catalog (career
outcomes, faculty, testimonials; requirements link OUT to catalog) — not redundant.

**Root cause → clean fix:** every `www.njit.edu` subsite is a separate Drupal install that publishes a
complete `/<section>/sitemap.xml`. Sitemap-driven crawling (Build A's pattern) has **no budget/depth to
guess** → it is complete and deterministic, so "recrawl is perfect" falls out for free.

## 3. Scope

**In scope:** ALL of `www.njit.edu` prose, driven by sitemaps — every subsite's own `sitemap.xml`
(offices + service subsites) **and** the main `www.njit.edu/sitemap.xml` (academics/marketing). One
host, captured completely, as `knowledge_items` prose under a new isolated source.

**Out of scope (deferred, flagged — not dropped):**
- **People (KG).** Office crawlers also create `Person` nodes + roles (KG layer). Build B is
  **prose-only** (like `catalog_crawl`/`college_crawl`) and does **not** touch `nodes`/`edges`. Office
  people stay exactly as they are.
- **Single-source consolidation.** Eventually one source should own each page. That consolidation
  (retiring the old office-crawl *prose* in favor of `www_crawl`) belongs to the planned **DB-wipe +
  Crawling-2.1 rebuild**, which will unify all crawlers. Build B is **additive + dedup** (§4.3), never
  destructive to other sources.
- **Pages not in any sitemap** (orphans). Sitemap-driven = complete w.r.t. the site's own published
  index; a true orphan page not listed in any sitemap is an accepted edge (the live-fallback covers the
  long tail on demand). Flagged, not silently assumed-covered.

## 4. Design (delta vs Build A)

### 4.1 New module `v2/core/ingestion/www_crawl.py`

A sibling of `catalog_crawl.py`, importing its host-agnostic primitives **unchanged**:
`extract_urls`, `_norm`, and (generalized — see §4.4) `catalog_seed_urls`. Reuses
`college_crawl.{ingest_college, ingest_pdf_pages, is_people_path, EntryResult}` and
`eos_crawl.extract_prose` exactly as Build A does.

New, isolated pieces:

1. **`WWW_SUBSITES` registry** — the curated list of `www.njit.edu` subsites + the main sitemap. One
   entry per crawl unit:
   ```
   WwwEntry(sitemap_url, org_slug, org_name, parent_slug, org_type)
   ```
   - Office subsites map to their **existing** org by slug (verified 2026-06-30): `/bursar`→`bursar`
     (17), `/registrar`→`registrar` (24), `/financialaid`→`financialaid` (28), `/careerservices`→
     `career-development` (18), `/counseling`→`counseling` (19), `/dos`→`dean-of-students` (20),
     `/graduatestudies`→`graduate-studies` (9), `/global`→`ogi` (16), `/admissions`→`graduate-admissions`
     (21); and the four EOS-family subsites `/environmentalsafety`, `/parking`, `/mailroom`,
     `/sustainability` all → `eos` (48) — exactly where their prose sits today. `ensure_org`
     early-returns for existing slugs → no new orgs, no tier changes. (The registry binds a
     *sitemap/URL-prefix* to an org; several URL-prefixes legitimately share one org.)
   - Service subsites get a **lightweight org under `njit`** (`type='office'`): `policies`, `finance`,
     `president`, `provost`, `reslife`, `publicsafety`, `studentinvolvement`, `writingcenter`, `eop`,
     `studyabroad`, `persistence`, `accessibility`.
   - The **main `www.njit.edu/sitemap.xml`** entry maps to `njit` root (academics/marketing is
     provenance-only; per Build A RAG-N4 the org map is not a ranking lever, so a single bucket is fine).
   **Adding/refreshing a subsite = one registry line + a recrawl** (no new code) — the repeatable-refresh
   property Build A established, generalized to many sitemaps.

   *Completeness note:* there is no NJIT master sitemap-of-subsites, so the registry is **curated**. The
   set above is the full known inventory (verified 2026-06-30). A genuinely new future subsite needs a
   one-line registry add; this limit is documented, not silent.

2. **`SOURCE = "njit_www_crawl"`** — distinct `created_by`. Reconcile is source-scoped, so it never
   cross-wipes the office crawlers (`created_by='crawler'`), `college_crawl`, `catalog_crawl`, `scholar`,
   or `dashboard`. (No collision with Build A's `catalog_crawl` either — different host, different source.)

3. **`crawl_www_entry(entry, fetch, fetch_bytes) -> (EntryResult, sitemap_urls)`** — per subsite:
   `urls = www_seed_urls(fetch_bytes, entry.sitemap_url)` → `extract_urls(urls, fetch)` (verbatim
   extraction, content-hash alias dedup, `is_people_path` skip — all reused). Each subsite is one org
   group (no `org_for` path-split needed — the registry already binds the whole subsite to one org), so
   ingest+release HTML per subsite bounds peak memory (Build A's N1).

4. **`run(conn, fetch, fetch_bytes, …)` orchestrator** — iterate `WWW_SUBSITES`; per entry: seed →
   extract → **cross-source content dedup (§4.3)** → `ingest_college(..., created_by=SOURCE)` +
   `ingest_pdf_pages(..., created_by=SOURCE)` for `.pdf` files → accumulate that subsite's sitemap URLs
   into a union set for reconcile.

### 4.2 Title handling

`www.njit.edu` Drupal pages already have a clean page `<h1>`/`<title>` (unlike CourseLeaf's generic
banner), and `eos_crawl.extract_prose` already takes the `<h1>` then the de-suffixed `<title>`. So Build
B uses `extract_prose`'s title **as-is** — it does NOT need Build A's `_catalog_title` override. (Spot-
checked in the dev dry-run, §7.)

### 4.3 Office overlap — cross-source content dedup (the one genuinely new behavior)

Offices already hold ~half of each office subsite's prose. To reach completeness **without** inserting
duplicate near-identical rows, Build B ingests a page **only if its content is not already active in the
corpus under any source**:

- `filter_existing_content(conn, res)` — for each extracted `ProsePage`, compute
  `sha1(page.content)` **with the same formula `ingest_college` uses** (`metadata.content_hash`), and
  drop the page if a row exists with `is_active=1 AND json_extract(metadata,'$.content_hash')=?`. Runs
  per subsite, **before** ingest, so `ingest_college` only ever sees genuinely-new-or-changed content.
- This is correct because every www source (offices, college_crawl, catalog, www_crawl) extracts via the
  **same** `eos_crawl.extract_prose` → identical bytes → identical hash for the same page, regardless of
  which URL alias it was reached by. So a page the office already captured (even under a `node/<id>` URL)
  is recognized and skipped; a page the office **missed** (e.g. `/bursar/payment-information`) is added.
- Net effect: **fill the gaps, skip the duplicates.** Completeness = (existing rows ∪ new `www_crawl`
  rows). Final single-source consolidation is the rebuild's job (§3).
- **Completeness is by CONTENT, not by every-URL-as-a-row (owner-confirm point).** If a sitemap URL's
  content already lives under an office `node/<id>`/`.php` alias, Build B skips storing the clean URL —
  the answer-bearing text is present, but the stored `source_url` (the user-facing source link) may be
  the office alias, not the clean `/bursar/payment-information` URL. This satisfies "whatever is on the
  site is in the DB" in the sense of *content*. The alternative (allow-overlap: store every sitemap URL
  even when content duplicates, for clean provenance links) trades duplicate near-identical rows for
  cleaner source links and is the rebuild's consolidation concern. Flagged for the reviewer/owner; the
  spec assumes content-completeness + dedup (the owner's chosen default #1).
- **Honest residual (flagged):** if an office holds a *stale* version of a page (different hash),
  `www_crawl` adds the *current* page (new hash) and the stale office row is **not** retired (office
  crawlers are change-detection-only, ND6) → a transient duplicate until the rebuild. This is the
  pre-existing office-staleness limitation, not worsened by Build B. Logged in the run summary.

### 4.4 Reconcile (generalized from Build A)

`reconcile_catalog` is generalized to `reconcile_sitemap_set(conn, sitemap_urls, prior_active_count, *,
created_by, min_floor=300, ratio=0.8)` — identical logic, but the `created_by` is a **parameter**
(Build A passes `CATALOG_SOURCE`; Build B passes `njit_www_crawl`). For Build B, `sitemap_urls` is the
**union of ALL subsite sitemaps + the main sitemap** gathered during the run, and `prior_active_count`
is the `njit_www_crawl`/`type='policy'` active count sampled **before** ingest. All of Build A's S1
guards carry over verbatim:
- empty union (all sitemap fetches failed) → skip retirement;
- `len(union) < max(300, 0.8 × prior)` → skip retirement (a partial/failed sitemap batch never
  mass-retires), logged loudly;
- `type='pdf'` excluded (B2 — PDF natural_keys are asset URLs, never sitemap `<loc>`s);
- `--limit`/`--no-reconcile` forces skip (S5).

This refactor is **additive**: `reconcile_catalog` becomes a thin wrapper calling
`reconcile_sitemap_set(..., created_by=CATALOG_SOURCE)`, so Build A is byte-for-byte unchanged in
behavior. (Build A's test suite is the regression guard.)

> **Reconcile + dedup interaction (explicit):** a page `www_crawl` **skips** via §4.3 is never inserted
> as `njit_www_crawl`, so it is outside the `njit_www_crawl` reconcile scope — `reconcile_sitemap_set`
> only ever retires `njit_www_crawl` rows. A skipped page therefore can't be wrongly retired, and a page
> present in the union but content-deduped simply stays owned by its original source. No interaction
> hazard.

### 4.5 Isolation & invariants (all from Build A, restated for the reviewer)

- `created_by='njit_www_crawl'` — source-scoped reconcile; never cross-wipes other sources or KG people.
- **Crawl = data-bringing only**: mechanical clean + verbatim text; no serving/gating/decline. Prose-only.
- **Never insert `search_text`** (generated). Embeddings via `embed_all.py` + `embed_chunks.py`.
- Gated live write: dev-copy → dry-run → `--commit` with `hardened_backup` → embed.
- Concurrency: serialize any live `--commit`/embed with the Scholar agent (single SQLite writer; shared
  `.backups/` rotation). Build B runs on its own branch + own dev DB copy (`/tmp/dev_buildb.db`).

## 5. Components & interfaces

- `v2/core/ingestion/www_crawl.py` — NEW. `WwwEntry`, `WWW_SUBSITES`, `SOURCE`, `www_seed_urls`
  (thin alias of the generalized sitemap parser), `crawl_www_entry`, `filter_existing_content`, `run`.
  Pure where possible (registry, dedup-filter take an injected `conn`) for unit testing without network.
- `v2/core/ingestion/catalog_crawl.py` — ONE additive change: extract `reconcile_sitemap_set` (generic
  `created_by`); `reconcile_catalog` delegates to it. No behavior change for Build A.
- `scripts/crawl_www.py` — NEW gated runner, mirrors `scripts/crawl_catalog.py` exactly. Flags: `--db`,
  `--commit`, `--embed` (runs `embed_all.py` then `embed_chunks.py`, passing `--db` through),
  `--delay`, `--entry <slug>` (crawl ONE subsite — dev/targeted; forces `--no-reconcile` since the
  frontier is partial), `--no-reconcile`, `--limit N` (dev: first N urls per entry; forces
  `--no-reconcile`). Sitemaps via `make_bytes_fetcher`; pages via `make_fetcher`. `hardened_backup`
  before any commit; dry-run default.
- `entry_points.py` — NOT modified (the registry lives in `www_crawl.py`, same call Build A made for
  the catalog).

## 6. Error handling (all inherited from Build A)

- A subsite's sitemap fetch fails / parses empty → that subsite contributes 0 URLs to the union; other
  subsites proceed; the §4.4 floor blocks retirement if the union collapses. Logged per subsite.
- A page fetch returns no HTML / `extract_prose` returns `None` → skipped + flagged, never stored.
- A PDF empty/image-only → manifest skip, no row (existing `ingest_pdf_pages`).
- `filter_existing_content` is read-only and order-independent; a DB hiccup surfaces as an exception
  before any write (dry-run default protects the live DB).

## 7. Testing (TDD)

Unit (no network; injected `fetch`/`fetch_bytes`; `:memory:` DB for dedup/reconcile):
1. `www_seed_urls` decodes a multi-`<loc>` sitemap, normalizes once (`_norm`), dedupes — reuses Build A's
   sitemap-parse tests (regression: generalization didn't change parsing).
2. `WWW_SUBSITES` registry: every entry has a sitemap URL + a resolvable org tuple; office slugs map to
   existing orgs; service slugs are `type='office'` under `njit`.
3. `crawl_www_entry`: one subsite → one org group; `is_people_path` skip; content-hash alias dedup;
   `EntryResult` carries that subsite's sitemap set.
4. **`filter_existing_content`**: a page whose `sha1(content)` already exists active (under a *different*
   `created_by`, e.g. simulated office row) is dropped; a genuinely-new page passes; hash formula matches
   `ingest_college` (a round-trip test: ingest a page as office source, then the same page is deduped).
5. Ingest writes `created_by='njit_www_crawl'`, `metadata.source='njit_www_crawl'`, `type='policy'`,
   correct `org_id`; idempotent re-run (no dup insert); changed content version-bumps. (Mirrors Build A
   test 4 under the new source.)
6. **`reconcile_sitemap_set`**: retires a `njit_www_crawl` policy row whose URL left the union; does NOT
   retire `type='pdf'`; empty union → retires nothing; below-floor union → retires nothing; **does NOT
   touch rows of other `created_by`** (the isolation test). Plus the regression: `reconcile_catalog`
   still behaves identically (delegation wrapper).
7. PDF skip-flag path + a PDF row survives a reconcile pass (B2 regression, reused).
8. Regression: Build A's full `test_catalog_crawl.py` still green (the reconcile refactor is additive).

Integration (gated, manual):
- Dev-copy (`cp gsa_gateway.db /tmp/dev_buildb.db`) dry-run, then `--commit` on the copy; inspect per-
  subsite counts; spot-check 2–3 page texts for nav/chrome pollution and that `/bursar/payment-information`
  now ingests with its real figures; confirm office overlap was deduped (no dup rows for already-held
  pages); `verify_kg`.
- Live `--commit` → **`embed_all.py` AND `embed_chunks.py`** (chunk vectors are load-bearing for the
  deep-fallback + answer-gate; whole-doc embed truncates long pages). If the embed invariant asserts on
  the pre-existing 16 vectorless chunks, **backfill those chunks** (do NOT `--force` the whole corpus) —
  see `project_catalog_scoping_followup`.
- **No-regression gate, pre vs post, required GO:** (a) `test_office_routing_gold` — no new office
  dilution; (b) `scripts/eval.sh` pre/post — coverage/accuracy not worse; (c) acceptance probes:
  `/bursar/payment-information` content (payment plan, $250 penalty), `/registrar/transcript`,
  `/financialaid/dates-and-deadlines` answer from the now-present pages.

## 8. Gated rollout

```
cp gsa_gateway.db /tmp/dev_buildb.db
python scripts/crawl_www.py --db /tmp/dev_buildb.db --entry bursar          # one-subsite dry-run
python scripts/crawl_www.py --db /tmp/dev_buildb.db                          # full dry-run
python scripts/crawl_www.py --db /tmp/dev_buildb.db --commit                 # dev write, inspect + verify_kg
python scripts/crawl_www.py --commit --embed                                 # live (hardened_backup; embed both)
bash scripts/ask.sh "njit payment plan late fee"                             # spot-check
```
DB-only change → no bot restart. Serialize the live `--commit`/embed with the Scholar agent.

## 9. Goals checklist (shipped / deferred — fill at PR)

- [ ] ALL `www.njit.edu` prose ingested as `njit_www_crawl` (every subsite sitemap + main sitemap).
- [ ] Sitemap-driven → complete + deterministic; office DFS page-gaps filled (`/bursar/payment-information`
      et al. present).
- [ ] Office overlap handled by cross-source content dedup (fill gaps, skip dups; no near-dup spam).
- [ ] Source isolation: `created_by='njit_www_crawl'`; reconcile never cross-wipes other sources or KG people.
- [ ] Repeatable refresh + retirement with Build A's S1 floor guards (partial sitemap never mass-retires).
- [ ] Chunk-embedded (`embed_chunks.py`) so deep-fallback + answer-gate see www content.
- [ ] No-regression gate GREEN (office gold + `eval.sh` + acceptance probes).
- [ ] Build A unchanged (reconcile refactor additive; its test suite green).
- [ ] **DEFERRED & FLAGGED** — KG people unchanged (prose-only); single-source consolidation → rebuild;
      orphan pages not in any sitemap → live-fallback; office-staleness transient dups → rebuild.

## 10. Risks

- **Sitemap drift / a subsite renamed** → its sitemap 404s → that subsite contributes 0 URLs; floor
  blocks retirement; clear per-subsite error. Re-point the registry line.
- **Curated registry misses a brand-new subsite** → one-line add when discovered; documented limit, not
  silent (§4.1). A future enhancement could auto-discover subsites from the main-site nav.
- **Cross-source hash mismatch** (extractor drift) → at worst a benign duplicate, never a wrong answer;
  rebuild consolidates. The shared `extract_prose` makes this unlikely.
- **Volume** (~1,800 sitemap URLs, ~1,000–1,300 net-new after dedup) → one-time ~10-min crawl at the
  default delay; single sequential pass, courteous.

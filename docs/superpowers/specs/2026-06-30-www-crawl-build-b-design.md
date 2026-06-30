# Build B — Complete www.njit.edu mirror (`www_crawl`) — Design

> Status: DESIGN (awaiting expert reviews + owner sign-off per the EXPERT-REVIEW HARD GATE).
> Date: 2026-06-30. Author: Claude (Opus 4.8), with Mohammad.
> **This is a LEAN DELTA-SPEC on top of Build A** (`2026-06-29-catalog-crawl-build-a-design.md`).
> It reuses Build A's proven sitemap-driven engine and only states what differs. Read Build A
> first; everything there (extraction reuse, S1 floor guards, S6 normalize-once, B1 bytes-fetcher,
> data-bringing-only hard line, gated rollout) applies here unchanged unless noted.

## 1. Goal (owner, 2026-06-30)

> "Whatever is on the NJIT site, we should have it in the DB — captured **once**, and every recrawl
> after that is **complete by construction**, with **no budget/depth limit on any host**. At the end
> of this session the crawling project is **done**." Build A did this for `catalog.njit.edu`. Build B
> finishes **every remaining NJIT host**, sitemap-driven.

**Scope expansion (owner, 2026-06-30, mid-session):** originally Build B targeted only `www.njit.edu`.
The owner then set the finish line as the **whole crawling project**: every NJIT page in the DB,
sitemap-driven, no budget/depth limit, recrawl-perfect. So Build B is now **one sitemap sweep over
ALL NJIT prose hosts** — `www.njit.edu` **and** every college/dept **subdomain** (cs / computing /
math / management / … — 22 hosts), which were previously crawled by `college_crawl`'s budget-limited
DFS (budget 400, depth-bounded) and are therefore not guaranteed-complete or deterministic-on-recrawl.
`catalog.njit.edu` (Build A) and `people.njit.edu` (`explore.py`, KG people) are already done and out
of scope.

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

**In scope:** ALL NJIT prose hosts, driven by sitemaps, as `knowledge_items` prose under one isolated
source:
- **`www.njit.edu`** — every subsite's own `sitemap.xml` (offices + service subsites) **and** the main
  `www.njit.edu/sitemap.xml` (academics/marketing).
- **Every college/dept subdomain** (22 hosts: cs, computing, math, mie, management, design, biomedical,
  informatics, datascience, engineering, theatre, …) — each crawled from its own `https://<host>/
  sitemap.xml`, mapped to its **existing** college/dept org (verified from live `college_crawl` rows,
  2026-06-30). Subdomains keep `classify_type` (NOT the `webpage` marketing override).

**Additive to `college_crawl` (the key safety property):** the subdomains already hold ~3,337
`college_crawl` (DFS) rows. The sweep is **dedup-fill**: it ADDS any sitemap page the DFS missed and,
via cross-source content dedup (§4.3), **skips everything already present** and **never retires a
`college_crawl` row** (reconcile is source-scoped — §4.4). Where the DFS found MORE than the sitemap
lists (orphan pages, e.g. math 1,286 rows vs a 239-URL sitemap), those rows are kept untouched. So the
sweep can only ADD coverage, never remove it. (Frontier ≈ 3,518 URLs total; most subdomain pages dedup
away, so net-new rows are modest. Single-source consolidation → rebuild, §3 deferred.)

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
   WwwEntry(sitemap_url, org_slug, org_name, parent_slug, org_type, page_type=None)
   ```
   `page_type` is an optional **mechanical** type override for the whole entry (default `None` →
   per-page `classify_type(url)` as today). It exists for the main-sitemap marketing bucket (§4.2,
   RAG-1) — a registry-driven, URL-derived label, NOT a content judgment, so it stays inside the
   data-bringing-only line (the *weight* of each type still lives in the retriever, not the crawler).
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
   - **College/dept subdomains** (22, from `_SUBDOMAINS`) → their **existing** college/dept org by slug
     (e.g. `cs.njit.edu`→`computer-science`, `computing.njit.edu`→`ywcc`, `math.njit.edu`→
     `mathematical-sciences`, `management.njit.edu`→`mtsm`, `design.njit.edu`→`hcad`,
     `theatre.njit.edu`→`theater-arts-technology`). Mapping verified from live `college_crawl` rows.
     `page_type=None` → `classify_type` (these are dept policy/news/event pages, not the marketing
     bucket). `ensure_org` early-returns (orgs exist) → no new orgs, no tier changes.
   - The **main `www.njit.edu/sitemap.xml`** entry maps to `njit` root (academics/marketing is
     provenance-only; per Build A RAG-N4 the org map is not a ranking lever, so a single bucket is fine)
     and is the one entry that sets **`page_type='webpage'`** (§4.2 / RAG-1): its 209 `/academics/degree`
     + 68 `/academics/major` + marketing landing pages are program *overviews* (career/salary/faculty/
     testimonials; requirements link OUT to catalog), so they must not compete at the `policy` 1.0 prior
     with the authoritative catalog requirement pages and office policy. `webpage` already exists and the
     retriever serves it at a 0.8 prior (downweighted, never excluded — honors verbatim/never-withheld).
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

### 4.2 Title & type handling

**Title:** `www.njit.edu` Drupal pages already have a clean page `<h1>`/`<title>` (unlike CourseLeaf's
generic banner), and `eos_crawl.extract_prose` already takes the `<h1>` then the de-suffixed `<title>`.
So Build B uses `extract_prose`'s title **as-is** — it does NOT need Build A's `_catalog_title` override.
(Spot-checked in the dev dry-run, §7 — incl. 2–3 `/academics/degree/*` + a marketing landing page, since
those use a different template than the office pages `_main_region` was tuned for — SE-5.)

**Type (one additive seam in `college_crawl`):** `ingest_college` gains a `force_type: str | None = None`
param — when set, it replaces the internal `classify_type(url)`; default `None` keeps every existing
caller byte-identical. `www_crawl` passes `force_type=entry.page_type` so the main-sitemap bucket lands
as `webpage` (§4.1) while every subsite keeps `classify_type` (so a subsite's `/news`//`/event(s)` pages
type correctly — SE-4 / RAG-4). `ingest_pdf_pages` is untouched (always `type='pdf'`). Verified: the
retriever excludes ONLY `publication` (`DEFAULT_EXCLUDE_TYPES={'publication'}`, retriever.py:143) and
serves `webpage` at `WEBPAGE_PRIOR=0.8` — so this **downweights, never hides** (honors never-withheld).
*Nuance:* the main sitemap also carries a few **substantive** cited pages (`/international-students`,
`/graduate-international-admissions-process`, `/about/senior-administration|maps-directions|history-njit`)
that take the same 0.8 downweight — still served, just under `policy`/catalog. If the dry-run audit shows
that hurts a real query, those specific paths can keep `classify_type` (a registry refinement); default
is the simple whole-bucket `webpage`.

### 4.3 Office overlap — cross-source content dedup (the one genuinely new behavior)

Offices already hold ~half of each office subsite's prose. To reach completeness **without** inserting
duplicate near-identical rows, Build B ingests a page **only if its content is not already active in the
corpus under any source**:

- **Prefetch the hash set ONCE (SE-3 — perf).** A per-page `WHERE json_extract(metadata,'$.content_hash')=?`
  is an **unindexed full scan** of ~24k rows × ~1,800 pages (no `content_hash` index — only
  `$.natural_key` is indexed). Instead, `run()` does ONE pass at start:
  `existing = { h for (h,) in conn.execute("SELECT json_extract(metadata,'$.content_hash') FROM
  knowledge_items WHERE is_active=1") if h }` → a Python `set`. `filter_existing_content(existing, res)`
  is then pure in-memory O(1)/page; **newly-ingested hashes are added to `existing` as the run proceeds**
  so within-run dupes across subsites are also caught.
- For each extracted `ProsePage`, compute `sha1(page.content)` **with the exact formula `ingest_college`
  uses** (verified identical across `ingest_college`/`eos_crawl`/`ingest_pdf_pages`; `content =
  clean_text(str(_main_region(soup)))`, and `_strip_recurring_assets` never touches `content` → the hash
  is batch- and alias-independent). Drop the page if its hash ∈ `existing`. Runs **before** ingest, so
  `ingest_college` only ever sees genuinely-new-or-changed content.
- **Stale-divergent detection (RAG-2).** Dedup catches only byte-identical content. If an office holds a
  *stale* version of a page (different hash — e.g. an old "$200 late fee" vs the current "$250"),
  `www_crawl` will ingest the current page and the stale office row is NOT auto-retired (office crawlers
  are change-detection-only, ND6) → both could co-rank. So `run()` **detects and reports** (does not act
  on — that would touch another source, breaking isolation) pairs where a www page's canonical-path
  identity matches an existing **other-source row in the same org with a different content hash**. The
  run summary lists these as "⚠ possible stale duplicate — review for gated retirement", a **manual gated
  follow-up** for high-value fee/deadline pages (the rebuild consolidates the rest). The run summary also
  reports the **count of pages dropped-as-dup** (SE-6) so the office-overlap dedup is observably firing.
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

### 4.4 Reconcile (generalized from Build A — with two correctness fixes)

`reconcile_catalog` is generalized to:
```
reconcile_sitemap_set(conn, sitemap_urls, prior_active_count, *, created_by,
                      seen_hashes=frozenset(), types=("policy",), min_floor=300, ratio=0.8)
```
identical core logic, but `created_by` and `types` are **parameters**. `reconcile_catalog` delegates with
`created_by=CATALOG_SOURCE, types=("policy",)` → **Build A byte-for-byte unchanged** (its test suite is
the regression guard). Build B passes `created_by='njit_www_crawl'`, `types=("policy","news","event")`
(SE-4 / RAG-4 — www subsites DO have `/news`//`/event` pages, so those rows must also be reconciled, not
silently immortal; `type='pdf'` stays excluded — B2). `sitemap_urls` is the **union of all subsite
sitemaps + the main sitemap** gathered during the run; `prior_active_count` is the
`njit_www_crawl`/`types` active count sampled **before** ingest.

Build A's S1 floor guards carry over, **plus two new guards Build B needs** because the single-sitemap
failure model becomes a many-sitemap one:

- **SE-1 — any-subsite-failure → skip ALL retirement.** With ~30 sitemaps under one global floor, a
  *single* subsite's sitemap 404ing (rename/blip) while the other 29 fill the union above the floor would
  let reconcile run and **mass-retire that whole subsite** (its URLs are now ∉ union, yet the floor
  "passed"). So `run()` tracks per-entry sitemap-fetch success and **skips the retirement pass entirely
  if ANY registry subsite returned an empty/failed sitemap** (logged loudly per subsite). Retirement is a
  yearly-rollover nicety; blocking it on any glitch is the correct "never mass-retire on a partial fetch"
  trade. (A future refinement could scope retirement per-successfully-fetched-subsite; the all-or-nothing
  skip is the minimal safe version.)
- **SE-2 — never retire a row whose content survives this run (dedup × rename content-loss).** Walk a CMS
  slug rename: content `C` moves from old `<loc>` `Ua` to new `Ub`. `Ua` left the sitemap (never
  crawled this run); `extract_urls` sees only `Ub`; `filter_existing_content` finds `sha1(C)` already
  active (the run-N row under `Ua`) → **drops `Ub`** (no insert); then naive reconcile sees `Ua ∉ union`
  → retires the `Ua` row → **`C` is now in NO active row. Lost.** Fix: `run()` collects the set of
  content-hashes seen in **this run's crawl** (every extracted page, pre-dedup) and passes it as
  `seen_hashes`; `reconcile_sitemap_set` **never retires a row whose `content_hash ∈ seen_hashes`** (its
  content is still present this run, just under an aliased/renamed URL). This is the invariant the old
  "no interaction hazard" note got wrong.

Other carried-over guards: empty union → skip; `len(union) < max(300, 0.8 × prior)` → skip (logged);
`--limit`/`--no-reconcile`/`--entry` force skip (S5 — a partial frontier never retires).

### 4.5 Isolation & invariants (all from Build A, restated for the reviewer)

- `created_by='njit_www_crawl'` — source-scoped reconcile; never cross-wipes other sources or KG people.
- **Crawl = data-bringing only**: mechanical clean + verbatim text; no serving/gating/decline. Prose-only.
- **Never insert `search_text`** (generated). Embeddings via `embed_all.py` + `embed_chunks.py`.
- Gated live write: **read-only dry-run (no copy) → `hardened_backup` → `--commit` → embed** (§8 — owner
  2026-06-30: no dev-copy cycle; the non-writing dry-run is the inspection and the backup is the rollback).
- Concurrency: the live `--commit`/embed runs only **after the owner has quiesced the Scholar agent and
  all other live writers** (owner 2026-06-30) — single SQLite writer, shared `.backups/` rotation, and a
  clean rollback point (nothing else to clobber). Build B is developed on its own branch.

## 5. Components & interfaces

- `v2/core/ingestion/www_crawl.py` — NEW. `WwwEntry`, `WWW_SUBSITES`, `SOURCE`, `www_seed_urls`
  (thin alias of the generalized sitemap parser), `crawl_www_entry`, `filter_existing_content`, `run`.
  Pure where possible (registry, dedup-filter take an injected `conn`) for unit testing without network.
- `v2/core/ingestion/catalog_crawl.py` — ONE additive change: extract `reconcile_sitemap_set` (generic
  `created_by`); `reconcile_catalog` delegates to it. No behavior change for Build A.
- `scripts/crawl_www.py` — NEW gated runner, mirrors `scripts/crawl_catalog.py`. Flags: `--db`,
  `--commit`, `--embed` (runs `embed_all.py` then `embed_chunks.py`, passing `--db` through),
  `--delay`, `--entry <slug>` (crawl ONE subsite — dev/targeted; forces `--no-reconcile` since the
  frontier is partial), `--no-reconcile`, `--limit N` (dev: first N urls per entry; forces
  `--no-reconcile`). Sitemaps via `make_bytes_fetcher`; pages via `make_fetcher`. `hardened_backup`
  before any commit; **dry-run default writes nothing** (uncommitted transaction discarded). The
  **dry-run is the inspection instrument** (since there's no dev copy now): it prints, per subsite, the
  kept/skipped/dropped-as-dup counts, the **`type` distribution** (so the recency-typing audit — RAG-4 /
  SE-4 — is done before any write), the **stale-dup ⚠ list** (§4.3), and a few **sample titles + first
  ~200 chars** (the nav-chrome spot-check — SE-5). `ingest_college` is called with `force_type=
  entry.page_type`.
- `entry_points.py` — NOT modified (the registry lives in `www_crawl.py`, same call Build A made for
  the catalog).

## 6. Error handling (all inherited from Build A)

- A subsite's sitemap fetch fails / parses empty → that subsite contributes 0 URLs to the union; other
  subsites' ingest proceeds (additive, non-destructive), but the **retirement pass is skipped for the
  whole run** (SE-1), so a single failed subsite never mass-retires its rows. Logged per subsite.
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
4. **`filter_existing_content` (prefetched set)**: a page whose `sha1(content)` is in the prefetched
   `existing` set (seeded from a *different* `created_by`, e.g. a simulated office row) is dropped; a
   genuinely-new page passes; a within-run dup (same content, two subsites) is caught via the
   incrementally-grown set; hash formula matches `ingest_college` (round-trip: ingest a page as office
   source, then the same page is deduped). **Assert the existence check is ONE prefetch scan, not N
   per-page queries** (SE-3).
5. Ingest writes `created_by='njit_www_crawl'`, `metadata.source='njit_www_crawl'`, correct `org_id`,
   `type` from `classify_type` for subsites **and `type='webpage'` when `force_type` is set** (the
   main-sitemap bucket, RAG-1); idempotent re-run (no dup insert); changed content version-bumps.
6. **`reconcile_sitemap_set`**: retires a `njit_www_crawl` row (policy/news/event) whose URL left the
   union; does NOT retire `type='pdf'`; empty union / below-floor union → retires nothing; **does NOT
   touch rows of other `created_by`** (isolation). **SE-1:** with a multi-subsite union, if one subsite's
   sitemap came back empty the pass is skipped (no mass-retire of that subsite). **SE-2:** a row whose
   `content_hash ∈ seen_hashes` is NOT retired even when its `source_url ∉ union` (rename content-loss
   guard). Regression: `reconcile_catalog` delegates and behaves identically.
7. PDF skip-flag path + a PDF row survives a reconcile pass (B2 regression, reused).
8. **`force_type` is additive**: `ingest_college` with `force_type=None` types via `classify_type`
   exactly as before (Build A callers unchanged); with `force_type='webpage'` overrides. Build A's full
   `test_catalog_crawl.py` still green (reconcile refactor + `force_type` are additive).

Integration (gated, manual — NO dev copy; read-only dry-run + backup, §8):
- **Read-only full dry-run on the live DB** (writes nothing): inspect per-subsite kept/skipped/dup
  counts; the **`type` distribution audit** (RAG-4/SE-4 — if a subsite like `president`/`studentinvolvement`
  carries dated news/events under a `policy` URL segment, add that segment to
  `classify_type`'s `_NEWS_SEGMENTS`/`_EVENT_SEGMENTS` **before** the live write, because re-crawl won't
  re-type unchanged content); **nav-chrome spot-check** of 2–3 office pages + 2–3 `/academics/degree/*`
  + a marketing landing page (SE-5); confirm `/bursar/payment-information` would ingest with its real
  figures; confirm the office-overlap **dedup is firing** (non-zero dropped-as-dup count, SE-6); review
  the **stale-dup ⚠ list** (RAG-2) for any high-value fee/deadline page needing a manual gated retire.
- `hardened_backup` → live `--commit` → `verify_kg` → **`embed_all.py` AND `embed_chunks.py`** (chunk
  vectors are load-bearing for the deep-fallback + answer-gate). If the embed invariant asserts on the
  pre-existing 16 vectorless chunks, **backfill those chunks** (do NOT `--force` the whole corpus) — see
  `project_catalog_scoping_followup`.
- **No-regression gate, pre vs post, required GO** (answer-gate ON): (a) `test_office_routing_gold` — no
  new office dilution; (b) `scripts/eval.sh` pre/post — coverage/accuracy not worse; (c) **underspecified-
  sibling probe set** (re-added from Build A §7c — RAG-3): "data science qualifying exam" (no level),
  "data science courses", DS-PhD vs CS-PhD vs Math-DS-MS — record the wrong-program rate, require **not
  worse than Build-A-post** (this build adds the most sibling-confusing surfaces, so this is the
  measurement that proves the §4.2 `webpage` demotion helped); (d) acceptance probes:
  `/bursar/payment-information` ($250 penalty, payment plan), `/registrar/transcript`,
  `/financialaid/dates-and-deadlines` answer from the now-present pages, and **only the current figure
  surfaces — no stale office duplicate co-ranks the top-k** (RAG-2).

## 8. Gated rollout (owner 2026-06-30: no dev-copy cycle — dry-run + backup)

The owner will **quiesce the Scholar agent + all other live writers before the live write**, so the live
DB is single-writer with a clean rollback point. No dev DB copy; the non-writing dry-run is the
inspection and `hardened_backup` is the rollback.

```
python scripts/crawl_www.py --entry bursar               # one-subsite dry-run (writes nothing) — sanity
python scripts/crawl_www.py                               # FULL read-only dry-run on live: inspect counts,
                                                          #   type distribution, dedup-drops, stale-dup ⚠,
                                                          #   nav-chrome samples (the §7 audit) — fix
                                                          #   classify_type/page_type if needed, re-run
# (owner confirms all other live writers stopped)
python scripts/crawl_www.py --commit --embed              # hardened_backup → live write → embed both
bash scripts/ask.sh "njit payment plan late fee"          # acceptance spot-check
# run the §7 no-regression gate (office gold + eval.sh + sibling probe + acceptance probes); GO/rollback
```
DB-only change → no bot restart. If the gate fails, restore the `hardened_backup` snapshot.

## 9. Goals checklist (shipped / deferred — fill at PR)

- [ ] ALL NJIT prose hosts ingested as `njit_www_crawl`: every `www.njit.edu` subsite + main sitemap
      **+ all 22 college/dept subdomain sitemaps** (the whole crawling project — no host left on DFS).
- [ ] Sitemap-driven → complete + deterministic everywhere (no budget/depth limit on any host); office
      DFS page-gaps filled (`/bursar/payment-information` et al.); subdomain sweep is additive dedup-fill
      (adds DFS-missed sitemap pages, never retires/loses a `college_crawl` row).
- [ ] Office overlap handled by cross-source content dedup (prefetched hash set, SE-3; fill gaps, skip
      dups; dropped-as-dup count reported, SE-6).
- [ ] Source isolation: `created_by='njit_www_crawl'`; reconcile never cross-wipes other sources or KG people.
- [ ] Repeatable refresh + retirement with the floor guards **plus SE-1 (any-subsite-fail → skip) and
      SE-2 (content-survives-this-run → never retire)** — partial sitemap / rename never loses content.
- [ ] Marketing bucket typed `webpage` (RAG-1); `type` distribution audited in the dry-run, news/event
      segments added where dated content hides under a `policy` URL (RAG-4/SE-4).
- [ ] Chunk-embedded (`embed_chunks.py`) so deep-fallback + answer-gate see www content.
- [ ] No-regression gate GREEN: office gold + `eval.sh` + **underspecified-sibling probe (RAG-3)** +
      acceptance probes incl. **no stale-dup co-rank (RAG-2)**.
- [ ] Build A unchanged (reconcile refactor + `force_type` additive; its test suite green).
- [ ] **DEFERRED & FLAGGED** — KG people unchanged (prose-only); single-source consolidation → rebuild;
      orphan pages not in any sitemap → live-fallback; office-staleness divergent-hash dups → detected +
      reported for manual gated retire (RAG-2), full consolidation → rebuild; provenance link may be the
      office alias not the clean URL on deduped pages (owner-accepted content-completeness, §4.3).

## 10. Risks

- **Sitemap drift / a subsite renamed** → its sitemap 404s → that subsite contributes 0 URLs; **the whole
  retirement pass is skipped (SE-1)**, never mass-retiring the failed subsite; clear per-subsite error.
  Re-point the registry line.
- **Page slug rename within a subsite** → content moves to a new URL; the SE-2 `seen_hashes` guard keeps
  the old row alive (content still present this run), so no content is lost between runs.
- **Curated registry misses a brand-new subsite** → one-line add when discovered; documented limit, not
  silent (§4.1). A future enhancement could auto-discover subsites from the main-site nav.
- **Cross-source hash mismatch** (extractor drift) → at worst a benign duplicate, never a wrong answer;
  rebuild consolidates. The shared `extract_prose` makes this unlikely.
- **Volume** (~3,518 sitemap URLs across all hosts; subdomain pages mostly dedup against existing
  `college_crawl` rows, so net-new is modest) → one-time ~18-min crawl at the default delay; single
  sequential pass, courteous.
- **Subdomain dual-source (www_crawl gap-fills + college_crawl bulk on the same host)** → benign:
  cross-source dedup prevents duplicate rows; reconcile is source-scoped so neither retires the other;
  full single-source consolidation is the rebuild's job. A subdomain whose org has a tiny minority of
  cross-linked rows under a *different* dept org (DFS following an inter-dept link) is unaffected — the
  registry binds each subdomain to its dominant/home org, exactly as `college_crawl` did.
- **More sitemaps under one floor (SE-1 amplified)** → with ~48 sitemaps, a single flaky host more often
  triggers the any-fail→skip-retire guard, so retirement runs only on fully-clean passes. Acceptable:
  retirement is a rollover nicety; the additive ingest always proceeds.

# NJIT Prose Harvest — Systematic Office/Service Crawl + Self-Extending Entry-Point Registry

**Status:** DRAFT design (brainstormed + expert-reviewed 2026-06-22; both reviews = PLAN-WITH-FIXES, all fixes
folded — see §11). Awaiting owner approval. Not built. Under the EXPERT-REVIEW HARD GATE. To be implemented by a
SEPARATE session (writing-plans → TDD build).
**Author:** Mohammad Dindoost (owner) + Claude (design).
**Related:** [[project_full_njit_mirror]] (the Phase-2 vision), the parking/EOS gap (a hand-run instance of the problem this systematizes), `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md` (the tiered-fallback precedent).

---

## 1. Problem (evidence-based)

Real student questions deflect because the KB is missing the **grad-student operational lifecycle** — immigration (OPT/CPT/I-20), registrar deadlines, billing, IT, dining, recreation, library services, parking, non-GSA clubs. Measured 2026-06-22 (shadow run + KB probes).

**Root cause is NOT (only) "pages we never visited" — it's pages we visited but only half-harvested, plus a prose crawler that was run by hand on a handful of offices:**

- **`crawler` (explore.py) — 19,447 items, people-only.** It visits office/department pages but extracts only *people* (bios, publications, awards) into the KG. The page's own prose — services, procedures, hours, policies — is **left unharvested**.
- **`njit-crawl` (the grounded prose pipeline) — 697 items, selective + manual.** It *does* harvest office prose well (Registrar: 463 items — the existence proof), but it was hand-run on ~4 offices (Registrar, Dean of Students 26, OGI 20, Bursar 13). **Every other office = 0.** It is a one-off manual op (violates the "no manual ops" principle); nothing re-visits pages to stay current.

Three distinct defects: **depth** (visited pages under-harvested), **coverage** (prose crawl ran on ~4 of N offices), **recurrence** (manual, never re-visited).

Example the design is built around: `https://www.njit.edu/global/` is a whole sub-site (visa advising, OPT/CPT, I-20/SEVIS, orientation, forms) but we hold only ~20 items — clearly under-harvested. It should be a registered entry point whose entire sub-tree is crawled and re-crawled.

## 2. Goals / Non-goals

**Goals (Phase 1):**
1. A **self-extending entry-point registry** (DB-backed, not hardcoded Python) that the crawler reads *and writes*.
2. Generalize an "entry point" to **(hub URL, scope prefix, aspect)** — add `aspect="office"` (prose→KB) alongside today's `aspect="people"` (→KG).
3. **Systematically + comprehensively** harvest each office hub's sub-tree into the KB (depth + coverage).
4. **Recurring, change-detected re-crawl** (no manual ops) — re-visit, re-embed only what changed.
5. Do all this **without diluting the curated answers** — the mirror lives in a **separate retrieval tier** (see §4.4).
6. Self-extension: links to *new* office hubs discovered during a crawl are **registered as candidate entry points** for the next run (gated activation — see open decision D3).

**Non-goals (Phase 1):** the full all-`*.njit.edu` subdomain mirror (that's Phase 2, which this architecture is designed to enable); changing the people-crawler (explore.py) — it stays as-is; live/current-info ("gym hours *today*") — that's the live-fallback's job, not static KB; events freshness — separate feed problem.

## 3. The core concept: a generalized, persistent entry point

Today (`v2/core/ingestion/entry_points.py`): `EntryPoint` is a frozen Python dataclass, all `aspect="people"`, hardcoded in `ALL_ENTRY_POINTS`. Adding a root = a code change.

Proposed: entry points become **rows in a DB table** the crawler reads and writes. An entry point is:
- **hub URL** — e.g. `https://www.njit.edu/global/`
- **scope** — the path prefix the crawl stays within (default = the hub's own path; `scope_prefix()` already computes this)
- **aspect** — `people` (→ KG, existing explore.py) or `office` (→ KB prose, new)
- **status** — `active` | `candidate` (discovered, awaiting activation) | `paused`
- **provenance + recurrence** — discovered_by/at, last_crawled_at, crawl_interval

**[SE4 fix] Phase 1 adds the table for `aspect="office"` rows ONLY.** Do NOT migrate the hardcoded
`aspect="people"` `ALL_ENTRY_POINTS` into the table now: those rows carry `kind` ('hub'|'listing'|'profile')
and `policy` (section-routing: `college_admin_only`, `hcad_split`) + ordering/children logic that the proposed
table omits and that `explore.py`/`run_explore.py` depend on — migrating them would **break the NCE/HCAD/MTSM
crawls**. So `ALL_ENTRY_POINTS` stays the people source unchanged; the DB registry drives only the new office
prose harvest. **People-migration is DEFERRED + loudly flagged** (a future unification that must first widen the
schema to carry `kind`/`policy`/ordering).

## 4. Architecture

### 4.1 Entry-point registry (`crawl_entry_points` table)
New STRICT table in `v2/core/database/schema.py`:
```
id, url, scope_prefix, aspect ('people'|'office'), org_slug, parent_slug, org_type,
status ('active'|'candidate'|'paused'), source ('seed'|'discovered'),
discovered_from_url, last_crawled_at, crawl_interval_days, created_at
```
A thin accessor module (`entry_point_store.py`): `list_active(aspect)`, `upsert_candidate(...)`, `mark_crawled(...)`, `activate(id)`. One writer per fact.

### 4.2 The crawl (reuse the machinery, add an aspect-aware link policy) — [SE1 fix]
`web_crawler.py::crawl_site(seed)` reuses the path-scoped BFS, dedup, budget/depth, SSRF guard,
robots-awareness, polite delay, and the 2026-06-22 malformed-page hardening. **BUT it is NOT free reuse
for office sweeps:** `select_links` only enqueues links that pass `is_relevant()` — a **people/research
vocabulary** (`publication/research/cv/lab/bio…`). An office sub-tree's nav (`/global/opt-cpt`,
`/parking/permits`, `/registrar/deadlines`) matches NONE of those, so a `/global/` seed would harvest
~1–2 pages, not the tree. **Required change:** an `aspect`-parameterized link policy — for `aspect="office"`,
follow **all same-scope HTML links** (drop the relevance gate; rely on `scope_prefix` + budget to bound),
and make **per-entry-point `budget`/`depth` configurable** (`DEFAULT_BUDGET=15` is far too small for an
office sub-tree). Correct the earlier "no new engine" framing: we reuse the engine + add a link policy + a
status-aware fetch (see §4.5). NOTE: `clean_text` strips `<nav>/<header>/<footer>` — may drop an in-page
service menu; acceptable for prose, add a spot-verify.

### 4.3 Ingest (hybrid, D1) — high-stakes pages are EXTRACT-ONLY [RA4] + a pre-ingest quality gate [RA5]
Each crawled page → `clean_text()` → **a pre-ingest quality/boilerplate gate** (deterministic, no LLM: drop
chunks below a min content-token count, high link-density / stopword-density, and near-duplicate nav/footer
blocks repeated across the sub-tree) → KB. Then hybrid:
- **Chunk-and-embed** generic prose pages (dining hours blurb, rec-center description) — existing chunker
  ≤350 tokens → `knowledge_items` (`source='crawler'`, **new `item_type='office_page'`**) → `embed_all.py`.
  **This leg is UNGROUNDED** (it goes through the normal generative compose path; only the extract leg gets
  verbatim grounding). Its safety rests ENTIRELY on the tier gate (§4.4) + `apply_headsup`. State this plainly.
- **LLM grounded-extract** (`grounded_extract`, verbatim spans + source link) for **all high-stakes
  procedural pages — OPT/CPT/I-20, deadlines, billing/dollar-amounts**. These are **extract-leg ONLY**, never
  the generative chunk leg, because a half-complete procedural chunk is the honest-partial failure mode.

### 4.4 Retrieval integration — the SEPARATE TIER (mechanism now specified, not asserted) [SE2, RA1–RA3, RA6]
The retriever excludes `webpage`/`publication` from the answer corpus on purpose — mass prose dilutes the
curated answers. The office prose gets the SAME protection, mechanically:
1. **`office_page` is added to `DEFAULT_EXCLUDE_TYPES` (code-level isolation, not just the mutable setting).**
   It NEVER enters the primary retrieve. **[RA1] The "RRF down-weight" alternative is DROPPED** — a soft prior
   is unsafe (a high-BM25 office hit can still out-rank a curated chunk; the removed `contact`-boost is the
   cautionary precedent) and mass office chunks crowd the fusion pool.
2. **The office tier is a SECOND `retrieve()` call scoped to `item_types=['office_page']`** (the shim's
   whitelist overrides exclusion) — searched in isolation, never co-ranked with curated content. No new engine.
3. **"Primary miss" = the EXISTING live-fallback signal** (`message_handler.py` ~628–630): `(not chunks) OR
   top_relevance(question, chunks) < LIVE_THRESHOLD (0.15)`. **[RA2]** Reuse `top_relevance` verbatim; do NOT
   invent a second threshold. A curated chunk scoring ≥ 0.15 is **never** displaced, by construction.
4. **[RA3] The office tier needs its OWN relevance floor** (`OFFICE_THRESHOLD`, suggest ≥ `LIVE_THRESHOLD`,
   tunable): if the best office chunk is below it, do NOT answer from office prose — fall through. Prevents a
   barely-relevant office chunk producing a confident wrong answer.
5. **[RA6] Explicit precedence ladder (resolves the two-fallbacks collision):**
   `structured KG → curated RAG (excl. office_page) → LOCAL office tier → LIVE Brave njit.edu → deflection`.
   **Local office tier BEFORE live Brave** — it's local/instant/no-API, draws from the same njit.edu source
   pre-harvested + verbatim, saves the shared Brave budget, and reduces live-fallback firing over time.
   **Response-flag plumbing:** an office answer is local KB → normal `source_note` + feedback buttons, `is_live=False`,
   and `attempted_live=False` so the user can still escalate to a live web search if it was thin.

### 4.5 Recurrence + change detection — retire ONLY on a confirmed 404/410 [SE3]
Per-URL content hash with the item; the recurring gated job (dashboard "Data Sources" + CLI) re-runs the
crawl per active entry point on its `crawl_interval`, re-`clean_text`s, **re-embeds only changed pages**.
**Footgun guard:** `make_fetcher` today returns `None` for ALL errors (it discards HTTP status), so a
transient outage/timeout/503 is indistinguishable from a real 404 — a naive "retire unseen pages" would
DELETE all good office content on a blip. **Required:** surface the HTTP status from the fetcher; retire a page
ONLY on a confirmed 404/410, and **NEVER retire on an empty crawl** (mirrors the people-crawler's
empty-decomposition / transient-fetch guard). Source-scoped reconcile (`created_by`/`item_type`-scoped).

### 4.6 Self-extension
During a crawl, links that look like *other office hubs* (heuristic: a section root under `www.njit.edu` not already in the registry) are written as `status='candidate'` rows with provenance. They do NOT auto-activate — owner (or a gated job) reviews `candidate` rows and activates them. This is the "crawler always knows the entry point for next time" behavior, made safe.

## 5. Two-layer relationship
- People-crawler (explore.py, `aspect="people"`) → KG (nodes/edges). **Unchanged.**
- Prose harvest (`aspect="office"`) → KB office-tier. **New.**
They complement; an office page contributes prose to the KB while any people on it are still handled by the people-crawler. No double-ownership (different aspects, different targets).

## 6. Scope phases
- **Phase 1 (this spec):** office/service hubs on `www.njit.edu`, each a registered `aspect="office"`
  entry point. **Starter set resolved in two waves (D5, owner 2026-06-22):**
  - **Wave 1 (prove the system + biggest wins):** **`/parking/` (EOS), `/global/` (OGI), `/bursar/`,
    `/registrar/` (deepen).** Chosen to exercise every risky path on a small high-value set —
    multisite/multi-prefix (parking), high-stakes extract-only (global/bursar/registrar), and the
    dilution tier. Parking is **entry-point #1** (recon ready — see the `feat/eos-parking-knowledge`
    branch + its reconciliation note).
  - **Wave 2 (after Wave 1 verifies in chat):** `/ist/` (IT), `/studentlife/`, recreation/WEC,
    financial aid, career development — plus **dining + library services ONLY if each has a real
    `www.njit.edu` hub** (vendor/subdomain hosts → Phase 2). Each Wave-2 hub URL is confirmed
    `www.njit.edu` at build time before it's registered.
- **Phase 2 (future, designed-for):** extend the registry to **all `*.njit.edu` subdomains** (the full mirror, [[project_full_njit_mirror]]) — same registry, same crawl, same tier; self-extension discovers subdomains. No re-architecture, just scale + a subdomain-discovery seed.

## 7. Anti-fabrication / quality
- `source='crawler'`; new `item_type='office_page'`; `search_text` stays generated (never inserted).
- LLM-extracted facts keep the verbatim-grounding filter (spans must appear literally on the page).
- Honest-partial preserved: the office tier is a *fallback*, so a thin/irrelevant prose chunk can't override a precise curated answer.
- Gated live writes (`hardened_backup` + `--commit`, dry-run + dev-copy first); embed after; DB-only → no restart.

## 8. Decisions (owner-resolved 2026-06-22)
- **D1 — ingest method:** ✅ **HYBRID** — chunk-embed every page + LLM grounded-extract only high-value pages.
- **D2 — tier mechanism:** ✅ **dedicated `office_page` type + "primary-miss → office-fallback"** (separate tier; no dilution).
- **D3 — self-extension activation:** ✅ **gated** — discovered hubs land as `candidate` rows; owner/gated job activates.
- **D4 — recurrence cadence:** **OWNER-CONFIGURABLE** — `crawl_interval_days` is a per-entry-point setting Mohammad sets; the build exposes it (default left blank/owner-set), not hardcoded. Job is dashboard-triggerable + schedulable.
- **D5 — Phase-1 starter office set:** ✅ **RESOLVED (owner, 2026-06-22) — two waves (see §6).**
  Wave 1 = **parking/EOS, global/OGI, bursar, registrar (deepen)** (covers multisite + high-stakes
  extract-only + dilution-tier on a small high-value set; parking = entry-point #1, recon ready).
  Wave 2 = IT/student-life/rec/financial-aid/career (+ dining/library iff a `www.njit.edu` hub).

## 9. Risks
- **Corpus dilution** — mitigated by the separate tier (§4.4); the single most important thing to get right.
- **Crawl politeness/load** at scale — bounded by budget/delay/robots; Phase 1 is a dozen sub-trees, modest.
- **Stale/duplicated content** — change-detection + source-scoped reconcile.
- **Scope creep into Phase 2** — explicitly fenced.

## 10. Goals checklist (to verify at build time — shipped/deferred)
- [ ] DB entry-point registry + accessor for **`aspect="office"` rows only** (people rows stay in
      `ALL_ENTRY_POINTS`; people-migration DEFERRED — [SE4])
- [ ] `aspect="office"` link policy (follow all same-scope links) + per-entry budget/depth — [SE1]
- [ ] Status-surfacing fetcher + reconcile that retires only on confirmed 404/410 — [SE3]
- [ ] Hybrid ingest → KB `office_page` type; **high-stakes pages extract-only**; **pre-ingest quality gate** — [RA4, RA5]
- [ ] Separate tier: `office_page` in `DEFAULT_EXCLUDE_TYPES` (code-level) + isolated `item_types=['office_page']`
      retrieve; miss = existing `top_relevance < LIVE_THRESHOLD`; office tier own floor `OFFICE_THRESHOLD` — [SE2, RA1–RA3]
- [ ] Precedence ladder `structured → curated RAG → local office → live Brave → deflection` + response-flags
      (`is_live=False`, `attempted_live=False`) — [RA6]
- [ ] Recurring change-detected re-crawl (gated job + CLI), owner-set `crawl_interval` — [D4]
- [ ] Self-extension: discovered hubs → `candidate` rows (gated activation)
- [ ] Phase-1 **Wave 1** offices harvested + verified in chat (**parking/EOS, global/OGI, bursar,
      registrar-deepen**); OPT/CPT + parking Qs added to `eval/questions.txt` **as a gate, not a
      footnote** — [RA3, D5]. Wave 2 (IT/student-life/rec/fin-aid/career, +dining/library iff www hub)
      after Wave 1 verifies.
- [ ] (DEFERRED, flagged) Phase 2 = full `*.njit.edu` subdomain mirror; people entry-point migration

---

## 11. Design-review record (2026-06-22)
Two background expert reviews on this spec — **both PLAN-WITH-FIXES** (architecture sound; the spec had
shipped its dilution-guard + honest-partial guarantees as *assertions*). All fixes folded above and tagged
`[SE#]` (senior-eng) / `[RA#]` (RAG/anti-fab):
- **SE1** office link policy + per-entry budget (the relevance gate would NOT sweep an office sub-tree).
- **SE2/RA1** code-level `office_page` exclusion + isolated whitelist retrieve; **RRF down-weight option DROPPED**.
- **RA2** "primary miss" = the existing `top_relevance < LIVE_THRESHOLD`; reuse, don't invent.
- **RA3** office tier needs its OWN relevance floor (`OFFICE_THRESHOLD`).
- **RA4** chunk-embed leg is ungrounded → high-stakes (OPT/CPT/billing) pages are extract-only.
- **RA5** pre-ingest boilerplate/quality gate.
- **RA6** explicit precedence ladder (local office BEFORE live Brave) + response-flag plumbing.
- **SE3** retire only on confirmed 404/410 (surface fetch status; never retire on empty crawl).
- **SE4** Phase-1 registry is office-rows-only; people-migration deferred (would break NCE/HCAD/MTSM).

*Next per process: owner reviews this revised spec → writing-plans skill → implementation plan → HARD GATE build
(handed to a separate implementation session).*

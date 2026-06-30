# Design — www_crawl as URL-keyed canonical prose owner (Crawling 2.1 "airtight")

**Status:** design, for expert review (senior-eng + RAG + Codex) before TDD build. HARD GATE.
**Author:** Claude (Opus 4.8), 2026-06-30. **Owner:** Mohammad.
**Branch/worktree:** `feat/www-crawl-buildb` @ `1f4d38f` (`.claude/worktrees/buildb-www-crawl`).

## 0. Context (what's already shipped, what's broken)
- **No DB-wipe rebuild is coming.** The original "wipe + single-pass rebuild" plan was **replaced** by
  ① split-ops (OPS db separated from KB/KG, live `eba7de9`) + **Crawling 2.1** (Build A catalog +
  Build B www). **Crawling 2.1 IS the fundamental data fix** — "capture all NJIT, once, recrawl-perfect."
  So defects in it must be fixed at root here, not deferred to a rebuild.
- **Build B already ran + committed + embedded** on the LIVE KB: 3,058 active `njit_www_crawl` prose/PDF
  rows (sitemap-driven sweep of www.njit.edu + all 22 college/dept subdomains). Sources now coexisting in
  the one KB (active counts): crawler 20071 (mostly PEOPLE decomposition + KG), college_crawl 3337,
  njit_www_crawl 3058, catalog_crawl 444, scholar 193, dashboard 95, migration 82.
- **DEFECT (the regression that triggered this):** Build B's dedup keys on **sha1(content)** ("skip a page
  whose content hash already active under any source"). That only catches BYTE-IDENTICAL pages. The same
  URL produces a different hash when (a) an older crawler captured a PARTIAL version, or (b) the page renders
  nondeterministically / appears in two subsite sitemaps. Result: duplicate rows for the same URL.
  - **21 same-URL cross-source collisions** (njit_www_crawl dup of an existing crawler/college_crawl/catalog
    row; www version is often the MORE complete one) + **32 self-dup URLs within njit_www_crawl** (mostly the
    same PDF in two sitemaps) ≈ **53 redundant rows / 3,058 (1.7%)**.
  - Observed harm: office-routing gold regressed (baseline office 10/12 → 9/12). New "Graduate Admissions"
    dup rows displaced the real Admissions office page out of top-2 for "which office handles graduate
    admission questions". (2 other gold misses — OPT, registration-hold — are PRE-EXISTING, not crawl-caused.)
- **Structural problem, not a one-off:** content-hash + additive ingest is **incompatible with
  "next crawl catches changes"** — when a page legitimately CHANGES, its hash changes, the dedup does not
  fire, and the page is INSERTED AGAIN rather than updating the existing row. Every change-catching recrawl
  re-accumulates dups. This grows unbounded.

## 1. Goal (owner directive, 2026-06-30)
One clean KB: **exactly one canonical row per NJIT page**, holding the **fullest correct content**, covering
the **whole NJIT site**, **losing nothing real**, and **recrawl-perfect**: the next crawl catches changes
(update changed / add new / retire gone) and NEVER re-accumulates duplicates. `www_crawl` is the **single
canonical owner of PROSE**. People/KG stay with `crawler`. Backup exists → free to retire/replace redundant rows.

## 2. Design — URL is identity; www_crawl owns prose; idempotent upsert

### 2.1 Page identity = `natural_key` (the URL), not content hash
Replace content-hash dedup with **URL-keyed upsert**. On (re)crawl of a sitemap URL set, for each URL:
- **new URL** (no active row under `njit_www_crawl` for that URL) → INSERT.
- **existing `njit_www_crawl` row, content changed** → UPDATE in place (new content, version bump, refresh
  content_hash + updated_at). Same row id — no dup.
- **existing `njit_www_crawl` row, content identical** → no-op.
- **URL in DB last run but ABSENT from this sweep's sitemap union** → retire (`is_active=0`) — gone-page reconcile.
This makes www_crawl idempotent and change-catching by construction (matches reconcile semantics, but keyed
on URL not content).

### 2.2 Cross-source: www_crawl is canonical for prose
Other crawlers captured prose pages too (incidental `crawler` prose like marketing landing pages;
`college_crawl` dept prose; `catalog_crawl` PDFs). To make prose single-source WITHOUT loss:
- **www_crawl must hold a row for EVERY prose URL it sweeps** (drop the cross-source content-hash SKIP that
  currently makes www defer to an existing row — that skip is exactly why 1,875 URLs have no www row today).
- After a complete sweep, **retire an old-source prose row IFF a www_crawl row now covers the same URL**
  (fail-closed coverage check — see §3 guard). Old rows whose URL www_crawl does NOT cover are KEPT (no loss).
- **NEVER touch:** `crawler` PERSON rows (person decomposition KB) + all `nodes`/`edges` (KG); `scholar`,
  `dashboard`, `migration` rows; OPS db. Only prose rows of crawler/college_crawl/catalog_crawl that www now
  owns by URL are retire-eligible. Distinguish crawler PERSON vs crawler PROSE by the person-entity linkage
  (root_id/parent_id to a Person node / entity decomposition) vs a standalone page row — define precisely in build.

### 2.3 Execution = Path A (re-crawl fresh)
Because www must own every prose URL (and 1,875 are currently un-owned by www), we **re-crawl all NJIT prose
fresh** with the §2.1 URL-keyed logic (no cross-source skip). This both (a) gives www complete coverage in one
pass and (b) proves idempotence. Then run the §2.2 cross-source retire (coverage-guarded). Re-embed
(embed_all + embed_chunks, self-healing). The fetch wall-clock deadline (`_read_capped`, committed `1f4d38f`)
de-risks the prior slow-drip hang.

## 3. Guards (non-negotiable — "lose nothing")
- **Coverage proof before any cross-source retire:** build the set of URLs www_crawl now actively owns; retire
  an old-source prose row ONLY if its URL ∈ that set. Fail-closed: any ambiguity → keep. Log every retire.
- **People/KG untouched:** assert crawler PERSON row count + nodes/edges count UNCHANGED before/after (hard check).
- **Source isolation:** scholar/dashboard/migration/OPS counts UNCHANGED.
- **Gated `hardened_backup` first** (online-backup + integrity); the existing pre-run backup
  `.backups/gsa_gateway.20260630-151310-236734.www-crawl.db` is the floor rollback.
- **Acceptance gate after:** office-routing gold ≥ baseline (no NEW regression; the 1 grad-admissions
  regression must clear), `verify_kg` clean, eval.sh no-regression, no same-URL dup remains (invariant query),
  no orphan chunk vectors (GC), embedded coverage 100%.
- **Verbatim/never-withhold preserved:** retiring a redundant duplicate is not withholding — the canonical
  (fuller) copy stays. No content is summarized/edited (crawl-cleans mechanically only, per hard line).

## 4. Idempotence / change-catching invariants (the recrawl-perfect promise)
- Re-running the full sweep twice with no site change → 0 inserts, 0 retires, 0 dups (proven by a double-run test).
- A page whose content changed since last sweep → exactly one UPDATED row (same id), not a second row.
- A page removed from all sitemaps → retired, not orphaned.
- One active row per `natural_key` across ALL sources (post-consolidation invariant, asserted in tests + gate).

## 5. Open questions for reviewers
1. **Cross-source retire safety:** is the §3 coverage-guard sufficient to guarantee zero prose loss, or is
   there a failure mode (e.g. a URL normalized differently across crawlers — trailing slash, %-encoding,
   node/<id> vs clean-URL alias) that would make "same page" miss and cause either a kept-dup or a wrong-retire?
   `natural_key` normalization is the crux — audit it.
2. **crawler PERSON vs PROSE split:** what's the exact, reliable predicate to never retire a person row?
3. **Path A vs B:** is a full fresh re-crawl justified, or can we reach the same lossless single-source state
   by consolidating the EXISTING committed rows in place (keep-fullest per URL) + switching ingest to URL-upsert
   for future runs (avoids re-crawl cost/risk this session)? Trade-offs?
4. **RAG/serving:** does collapsing to one fuller row per URL change retrieval distribution in a way that needs
   re-tuning (recency typing, webpage 0.8 prior, dedup-before-RRF)? Any risk the "fuller" row is noisier (more
   nav/boilerplate) and ranks worse than the tighter old row it replaced?
5. **Scale/perf:** ~4,900 prose URLs re-crawled + re-embedded; any concern vs the embed self-healing pass?

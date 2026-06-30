# Design rev3 — Day-1 scoped PROSE rebuild (Crawling 2.1 airtight, single-canonical)

**Status:** rev3 — folds the rev2 RE-REVIEW (senior-eng GO-WITH-CHANGES + RAG GO-WITH-CHANGES; both "no
architecture remains, all spec-precision"). Supersedes rev1 (`…canonical-prose-owner-design.md`, NO-GO).
**rev3 changes:** §4.4 density = boilerplate-stripped LENGTH (not unique-token ratio — was backwards);
§5.1 coverage gate now CONTENT-AWARE + canonicalizes the BACKUP side (the wipe-window under-capture guard);
§5.4 rank-preservation baseline = backup DB over eval+office query set; §5.5 GUARD widened to ~10 general Qs.
Ready for owner sign-off → TDD build. HARD GATE.
**Author:** Claude (Opus 4.8), 2026-06-30. **Owner:** Mohammad (approved this direction).
**Branch/worktree:** `feat/www-crawl-buildb` @ `f679cc1` (`.claude/worktrees/buildb-www-crawl`).

## 0. Why rev2 — what the 3 reviews changed
Rev1 proposed making `www_crawl` canonical by **mass cross-source RETIRE** of ~3,314 legacy
`college_crawl`/`crawler`/`catalog_crawl` prose rows, matched by URL against fresh www rows. All three
reviewers (RAG GO-WITH-CHANGES, Codex NO-GO, senior-eng NO-GO) converged: the **cross-source retire is the
risk** — URL-identity matching against legacy rows is unsolved (`node/<id>` aliases, trailing-slash/`%`-encoding
normalization mismatches, **sitemap coverage ⊉ DFS coverage**), and at 3,314 retires a small error rate =
permanently dropped pages.

**Owner reframe (the fix): treat this as DAY 1 of the prose corpus.** We have a full backup and no active
users. So instead of retiring legacy, we **wipe crawl-sourced PROSE and rebuild it fresh** with the correct
URL-keyed canonical logic from the start. **This removes the #1 blocker entirely** — there is no legacy to
match/retire; cross-source identity matching collapses to **within-run dedup** under one uniform normalizer.
The remaining risks (coverage, within-run alias, truncation) are tractable and are gated on a **dev copy
before any atomic swap**.

## 1. Goal (unchanged)
One clean KB: **exactly one canonical row per NJIT page**, **fullest correct content**, **whole NJIT site**,
**lose nothing real**, **recrawl-perfect** (next crawl catches changes — update changed / add new / retire gone
— never re-accumulating dups). PROSE is single-canonical. People/KG + manual content preserved verbatim.

## 2. Scope — what is wiped vs preserved (PROSE-only "day 1")
**WIPE (rebuild from fresh crawl):** active prose rows of the crawl prose engines —
- `created_by IN ('njit_www_crawl','college_crawl','catalog_crawl')`, AND
- `created_by='crawler' AND json_extract(metadata,'$.entity_id') IS NULL` (crawler INCIDENTAL prose;
  senior-eng finding #1: `crawler/policy` = 624 rows, 0 with entity_id = the only incidental crawler prose).

**PRESERVE (NOT re-derivable from a prose crawl — never touched):**
- ALL `crawler` PERSON rows = `created_by='crawler' AND entity_id IS NOT NULL` (incl. `crawler/webpage`
  personal sites, which DO carry entity_id — the entity_id predicate keeps them; a type predicate would not).
- The ENTIRE knowledge graph: `nodes` + `edges` (people/orgs/research-areas) — untouched.
- `scholar`, `dashboard` (manual GSA/clubs/officers), `migration` rows.
- The OPS db (posts/deliveries/judging) — separate DB, out of scope.
- `knowledge_vectors`/`knowledge_chunks`/`knowledge_chunk_vectors` for preserved rows; only wiped-row vectors
  are GC'd (existing `vector_gc` orphan sweep).

**Hard invariants asserted before/after (fail-closed):** preserved person-row count, `nodes` count, `edges`
count, and scholar/dashboard/migration counts are **byte-for-byte unchanged**. People/KG untouched is proven,
not assumed.

## 3. The rebuild crawl (fresh, into a dev copy)
Run the prose engines fresh against a **dev copy** of the live DB (with prose wiped per §2):
- **Engines:** `www_crawl` (all-hosts sitemap sweep: www + 22 subdomains) + `college_crawl` (DFS over college
  subdomains) + `catalog_crawl` (catalog.njit.edu). We KEEP college_crawl/catalog in the rebuild (not www-only)
  **specifically to guarantee coverage ≥ DFS** (RAG/SE/Codex: sitemaps can miss DFS-only pages). Overlap across
  engines is handled by the global dedup (§4), not by a fragile retire.
- The committed fetch wall-clock deadline (`_read_capped`, `1f4d38f`) prevents the prior slow-drip hang.

## 4. The learned fixes (fold ALL review findings) — the canonical-prose write path
A single shared module owns prose identity + dedup so all three engines behave identically.

### 4.1 One canonical URL normalizer (RAG#5, Codex#1/#2, SE#5)
`canonical_prose_url(url)`: scheme→https, lowercase host, strip trailing slash, drop fragment; **keep query**
unless on a vetted-noise allowlist (Codex#3: dropping `?audience=…` could collapse distinct pages → never drop
by default). Applied identically by **every** engine (today `catalog/_norm` strips slash, `college/normalize_url`
keeps it — unify). Stored as the row's `natural_key`. **Enforced** by a partial unique index on active prose
rows keyed on `canonical_prose_url` (Codex#1) — the invariant is DB-enforced, not convention.

### 4.2 `node/<id>` ↔ clean-URL alias resolution (Codex#2 BLOCKER, RAG#5)
String normalization alone cannot equate `…/node/140` and `…/undergraduate-thesis-option`. Resolve aliases by
**evidence**: `<link rel="canonical">` on the page, HTTP redirect target, or Drupal alias metadata. Persist an
alias map (`alias_url → canonical_url`, with the evidence/source). **Ambiguous aliases STAY ACTIVE** (never
guess-collapse). Within-run, two URLs proven aliases → one row (the canonical/clean URL).

### 4.3 URL-keyed canonical upsert, GLOBAL across orgs (RAG#3, SE#2, Codex#1)
Idempotency keyed on `canonical_prose_url` **across all orgs/sources** (NOT today's `(org_id, natural_key,
created_by)` — that lets the same URL land as `policy@bursar` + `webpage@njit`). Within a run, a URL→single
(org,type) assignment, **first-typed wins**; the main-sitemap marketing/`webpage` bucket only fills URLs no
typed entry claimed (kills the webpage-vs-policy twin that caused the regression).

### 4.4 Keep-fullest on collision AND on update (RAG#1 BLOCKER, SE#2/#4, rev2-RAG#1)
When two captures resolve to the same canonical URL (within-run, or a recrawl update): **keep the
higher-quality** row, do NOT blindly adopt the latest. **Quality metric = BOILERPLATE-STRIPPED CONTENT LENGTH**
(token count AFTER `_strip_recurring_assets` + boilerplate-signature pass) — i.e. "fullest of *real* content."
**NOT type-token / unique-token ratio** (rev2-RAG#1: TTR is length-biased toward the SHORTER capture — the
opposite of the goal). **NOT raw length** either (RAG#2: raw-longest can be more nav/boilerplate; CE truncates
@512 + bm25 length-penalty make raw-longest rank worse). **Tiebreak:** lower boilerplate fraction (leading-nav
penalty — proxy for "answer not pushed past the CE 512-token window"), then most-recent. **Never let a
`type='webpage'` row supersede a `policy/news/event` row** (SE#2/#4: the live `graduate-admissions` trap). A
shorter/thinner re-fetch (e.g. truncated under the fetch deadline) does NOT overwrite a fuller row — guard +
logged warning. (NOTE: in rev2 all engines re-crawl through the SAME `extract_prose`/`_strip_recurring_assets`,
so within-run same-URL captures are near-identical; this rule only bites on genuinely-different content.)

### 4.5 PDF dedup (SE#3 BLOCKER, RAG/Codex)
PDFs currently key `(org_id,url)` and `reconcile` EXCLUDES `'pdf'` → the 32 self-dups are structural. Fix:
global `seen` set keyed on `canonical_prose_url` for PDF assets within a run (first org wins), and include PDFs
in the gone-page reconcile keyed on the asset-URL union. Invariant covers PDFs too.

### 4.6 Recrawl-perfect going forward (RAG#4, Codex#4, SE#7)
Because all prose engines now write through the §4 canonical path, a future recrawl of ANY engine is
idempotent + change-catching: unchanged → no-op; changed → update the canonical row in place (+ chunk/vector
invalidation, SE#4/Codex#4); URL gone from the sweep union → retire. No cross-engine re-accumulation, because
identity is the global canonical URL, not the source.

## 5. Validate on the dev copy → atomic swap (the safety gate)
Build the rebuilt DB on a copy; **prove before swapping**:
1. **Coverage (lose-nothing) gate — CONTENT-AWARE, both sides canonicalized** (rev2-SE#1/#2, rev2-RAG#2):
   - **Canonicalize BOTH sides first:** run the BACKUP rows' stored `natural_key`s through the NEW
     `canonical_prose_url` + the §4.2 alias map before comparing (backup keys were written under the OLD
     non-uniform normalizers — catalog strips slash, college keeps it — so a raw compare false-FAILs en masse
     OR masks loss). Compare canonical-vs-canonical.
   - **URL coverage:** rebuilt canonical-prose-URL set **⊇** backup canonical set, modulo a reviewed, logged
     drop-list (proven soft-404s). Any backup prose URL absent from the rebuild and not on the drop-list = FAIL.
   - **CONTENT coverage (the wipe-window guard, rev2-SE#1):** for every covered backup URL, the rebuilt row's
     **boilerplate-stripped content length ≥ the backup row's** (within a small tolerance), minus the drop-list.
     A thinner rebuilt row (truncated/under-capture — deadline-cap, transient 404, host-down) = FAIL. This is the
     ONLY defense against silent content loss the URL-set gate can't see (post-wipe there is no within-run
     baseline; the backup IS the baseline). Log every URL where rebuilt < backup with both lengths.
2. **People/KG/manual untouched:** §2 invariant counts byte-identical vs backup.
3. **Single-canonical invariant:** ≤1 active prose row per `canonical_prose_url` across ALL sources (incl PDFs).
4. **Anti-corank + regression probes (RAG#6):**
   - anti-corank: for the formerly-dup URLs, exactly one active row, and it is the canonical/densest one;
   - grad-admissions serving probe: "which office handles graduate admission questions" → real Admissions
     office page rank-1/2, no `Graduate Admissions` dup anywhere in top-k;
   - rank-preservation diff (rev2-RAG#3): baseline = the **BACKUP DB** over the **eval.sh + office-gold query
     set** (NOT "formerly-dup rows" — no stable row identity post-wipe). Capture each query's answer-bearing-
     content rank R against the backup PRE-swap; re-run against the rebuilt DB; assert ≤ R+tolerance. Proves
     "fuller/consolidated didn't rank worse" and that the marketing `webpage` bucket didn't dilute.
5. **office-routing gold ≥ baseline (10/12); GUARD ≥ baseline — widen GUARD to ~10 general (non-office) queries**
   (rev2-RAG#4) so a per-query dilution swap can't hide inside the eval.sh aggregate; **eval.sh no-regression;
   verify_kg clean; embed coverage 100%; 0 orphan chunk vectors.**
6. **Idempotence double-run (SE#7, Codex#4):** re-run the full rebuild on the rebuilt DB → 0 inserts / 0 retires
   / 0 dups; test fixtures MUST include a PDF-in-two-sitemaps and a URL-in-two-sitemaps case.
7. **Answer-gate note (RAG#7):** the parked answer-gate band must be re-checked/refit on the consolidated
   corpus before it is ever turned on (out of scope to ENABLE here; flagged).

Only on ALL-PASS: `hardened_backup` (floor rollback exists: `.backups/gsa_gateway.20260630-151310-236734.www-crawl.db`)
→ atomic swap dev→live (no users) → re-verify on live → commit → owner sign-off → merge `feat/www-crawl-buildb`.

## 6. Goals check (rev2)
- one canonical row per URL — **achieved** (§4.1 enforced index, §4.3 global key, §4.5 PDFs, §5.3 invariant).
- fullest correct content — **achieved** (§4.4 density + never-webpage-supersedes-policy + truncation guard).
- whole NJIT, lose nothing — **achieved + PROVEN** (§3 multi-engine coverage, §5.1 coverage gate fail-closed).
- recrawl-perfect — **achieved** (§4.6 canonical-keyed upsert, §5.6 double-run incl PDFs).
- people/KG/manual untouched — **achieved + PROVEN** (§2 entity_id predicate, §5.2 invariant).

## 7. Open questions for Codex re-review
1. Does the **day-1 wipe+rebuild** framing actually close your rev1 BLOCKERs #1–#3 (URL identity / node-alias /
   cross-source retire loss), given there is now NO legacy retire — only within-run dedup + a coverage gate?
2. Is the **§5.1 coverage gate** (rebuilt canonical-URL set ⊇ backup canonical-URL set, fail-closed) sufficient
   to guarantee zero prose loss, or is there a hole (e.g. an alias that normalizes differently between the
   backup rows and the fresh rows, making a covered page look uncovered → false FAIL, or worse, masked loss)?
3. Is keeping `college_crawl`+`catalog_crawl` in the rebuild (for DFS coverage) + global canonical dedup the
   right way to avoid sitemap⊉DFS loss, vs www-only?
4. Any remaining path where a recrawl re-accumulates a dup or the keep-densest rule picks the wrong row?

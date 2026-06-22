# EOS / Parking Knowledge Gap — Design

**Date:** 2026-06-22
**Status:** DESIGN (pending expert review + Mohammad approval)
**Author:** Claude (Kavosh session)
**Branch:** code work in a worktree off `feat/kavosh-v2.1-phase0-router-bakeoff` (live branch)

## Problem

The KB has no authoritative parking / campus-operations content. Verified 2026-06-22:
`knowledge_items` matching "parking" returns only faculty research papers;
"locksmith"/"mailroom" return 0 rows; there is no Org node for the operations department.
njit.edu answers these everyday grad-student questions; we can't:
"where do I park", "how much is a permit", "visitor parking", "campus shuttle",
"how do I get my photo ID", "who do I call for a lockout", "where's the mailroom".

**Source of truth:** `https://www.njit.edu/parking/` — the hub for the **Environmental and
Operational Services (EOS)** department, which owns: Parking, Photo ID, Visitor Parking,
Transportation, Security Systems, Locksmith, Mailroom, SchoolDude Work-Order System,
Environmental Health & Safety, Office of Sustainability.

## Key finding (drives the crawler change)

The `/parking/` site is a **separate Drupal multisite (`njit.edu.parking`)**. Verified: it is
**absent from both NJIT sitemaps** (`www.njit.edu/sitemap.xml`, `catalog.njit.edu/sitemap.xml`)
— 0 `/parking` URLs. So the existing sitemap-driven `_crawl_stage.py --bucket parking` stages
**zero** pages.

The hub `https://www.njit.edu/parking/` fetches cleanly (~67 KB) and links to ~30 same-site
`/parking/*` sub-pages plus cross-site `/mailroom/`, `/sustainability/`, `/environmentalsafety`,
`/about/transportation-campus`. A **seed-URL + one-level link-follow** staging mode reaches
everything. The rest of the grounded pipeline (verbatim extract → ground-filter → gated ingest →
embed) is unchanged.

## Decisions (confirmed with Mohammad 2026-06-22)

1. **Org modeling:** ONE EOS Org node (`type='office'`, parent `njit`); the 10 service areas
   are **KB topics**, NOT child orgs. Rationale: service areas are functions, not orgs with
   people/roles — child orgs would be empty shells (fabricated structure). Contacts/locations
   are captured as **KB prose**, not Person nodes (honest-partial: these are office contacts,
   not modeled people).
2. **Crawler reach:** Add a reusable **seed-URL + 1-level link-follow** mode to `_crawl_stage.py`.

## Architecture / components

### 1. EOS Org node (graph layer) — one-time gated step
- `ensure_org(conn, slug="eos", name="Environmental and Operational Services (EOS)",
  parent_slug="njit", type="office")` → `part_of njit` via `sync_org_nodes`.
- **NO `metadata.aliases`** (revised after expert review). The org name resolves itself and the
  **parenthetical `(EOS)`** makes the resolver answer "EOS" for free. We deliberately do NOT
  alias "parking"/"parking office": an alias would resolve `org_id=EOS` for any query containing
  "parking", and `router.py:351` would then fire `people_in_org(EOS)` on a "staff/team/leadership"
  cue → EOS has no Person nodes → a confident **"I don't have people listed for EOS"** deflection
  (`structured_answer.py:285`) that does NOT fall through to RAG, defeating the exact questions
  this work targets. "parking"-the-topic is answered by **RAG/FTS over the EOS KB prose** (which
  literally contains the word), not by org resolution. (This also makes the previously-proposed
  `metadata IS NULL` UPDATE moot — it could never match `metadata NOT NULL DEFAULT '{}'` anyway.)
- An **"About EOS"** `knowledge_item` (verbatim from the hub's intro, spelling out
  "Environmental and Operational Services (EOS)" so the FTS leg keys on it) anchors "what is EOS".

### 2. `_crawl_stage.py` — new seed/link-follow mode (CODE CHANGE, hard gate)
Add, alongside the existing `--bucket` sitemap mode (kept intact):
- `--seed URL[,URL…]` — one or more seed/hub URLs (repeatable or comma-separated).
- `--follow PAT[,PAT…]` — path **prefix** patterns (revised: prefix-anchored, not loose
  substring); a link is kept iff **same host** AND its path `startswith` one of the patterns.
- `--limit N` — page-count cap for the seed mode too (runaway backstop; default a sane cap).
- Behaviour (depth 1): fetch each seed → stage the seed page itself → extract `href`s →
  normalise → keep same-host + `--follow` matches → **drop assets** (`.css .js .ico .png .jpg
  .pdf …`, `?` query-include links, `#fragment`) → dedupe → fetch + `clean_text` each → if the
  cleaned body is empty/near-empty (`chars < MIN`, the JS-only SchoolDude/visitor-app shells)
  **log a SKIP and do not stage** (makes the "skipped & flagged" promise real) → write
  `/tmp/njit_crawl/<prefix>__<slug>.txt` with the `SOURCE_URL:` header → manifest.
- **Reuse, don't reinvent (review B2):** normalisation + host check use the existing
  `web_crawler.normalize_url` / `same_site` (they already handle protocol-relative `//host`,
  relative `/path`, absolute, fragment + query drop, host-lowercasing). Fetch routes through
  **`web_crawler.make_fetcher`** (robots.txt-aware + SSRF-guarded) for parity, since the seed
  mode follows arbitrary on-page hrefs (vs the trusted sitemap mode).
- `--bucket` and `--seed` are mutually-exclusive (exactly one per run; `--bucket` drops its
  `required=True`); `--prefix` required for both.
- **New pure helper for tests (renamed to avoid the existing `web_crawler.select_links`):**
  `select_seed_links(base_url, html, follow_prefixes) -> list[str]` (normalise via
  `normalize_url` + `same_site` + prefix-match + asset-drop + dedupe). Unit-tested with fixture HTML.

Planned invocation:
```
python scripts/_crawl_stage.py \
  --seed https://www.njit.edu/parking/ \
  --follow '/parking/,/mailroom/,/sustainability,/environmentalsafety,/about/transportation' \
  --prefix eos
```

### 3. Verbatim extraction (existing manual pipeline step)
A subagent extracts **verbatim** facts from each `/tmp/njit_crawl/eos__<slug>.txt` into
`bot/data/sources/njit-web/eos__<slug>.md` with front-matter `title`, `source_url`, and a new
**`org: eos`** key. **The `.md` stem MUST equal the staged `.txt` stem** (review S6) or
`_crawl_ground_filter.py` (which looks up `STAGE/<stem>.txt`) skips the doc. **`title` is the
service name** ("Visitor Parking", "Mailroom", "Photo ID Office", "Campus Shuttle /
Transportation", …) so the FTS leg (`search_text = title || ' ' || content`) keys on the user's
word (review S2). Extractive only — no paraphrase, no invented numbers; a span that isn't
literally on the page is dropped (the ground-filter then re-enforces this).

### 4. `_crawl_ground_filter.py --apply` (unchanged)
Keeps only lines that appear VERBATIM (whitespace-normalised substring) on the staged page;
drops empties. Anti-hallucination gate.

### 5. `_crawl_ingest.py` — per-doc org (CODE CHANGE, hard gate, small)
Currently files every njit-web doc under the `njit` root. Change: read an optional
**`org:` front-matter slug** per doc; file that doc under the named org's id (the org **must
already exist** — error if missing, never auto-guess metadata). Default (no `org:`) stays
`njit`, so existing docs are unaffected. To avoid touching the shared `parse_front_matter`
3-tuple signature (used by `ingest_office_docs.py` too — review N), add a **small local
`_read_org(text)` reader** in `_crawl_ingest.py` that greps the front-matter block for `org:`.
Re-runs are move-safe because `upsert_doc_items` retire is `doc_id`-scoped (review N7). Source
stays `'crawler'`, `doc_type='reference'`. Gated (`hardened_backup` + `--commit`, dry-run default).

### 6. Operations heads-up topic (CODE CHANGE, hard gate, small) + source link
- Add a `Topic("operations", "Parking / EOS office", (parking|permit|shuttle|lockout|mailroom|
  photo id|visitor parking|…))` to `bot/core/headsup.py` (currently only immigration/billing/
  funding) so fee/hours/contact answers get a "confirm with the parking office" line — the
  staleness mitigation for volatile prices and the deferred re-crawl.
- Confirm each EOS doc's `source_url` front-matter is carried to the retrieved chunk and
  surfaced as the verify-link, so a possibly-stale KB price always ships with its authoritative
  `njit.edu/parking/...` source (review S1/S3).

### 7. Embed + verify
`python v2/scripts/embed_all.py` (resumable). Then chat verification (below).

## Data flow

```
hub URL ──_crawl_stage --seed/--follow──▶ /tmp/njit_crawl/eos__*.txt (verbatim clean_text)
        ──subagent verbatim extract────▶ bot/data/sources/njit-web/eos__*.md  (front-matter: title, source_url, org: eos)
        ──_crawl_ground_filter --apply─▶ same files, only literal-on-page lines kept
        ──_crawl_ingest --commit───────▶ knowledge_items (org=eos, source='crawler', doc_type='reference')
        ──embed_all────────────────────▶ knowledge_vectors
EOS org node ── ensure_org + sync_org_nodes + metadata.aliases (one-time gated)
```

## Error handling / invariants
- **Gated live writes:** every DB write takes a `hardened_backup`, defaults dry-run, needs
  `--commit`. Dev-copy (`/tmp/dev.db`) first, inspect, then live.
- **source tags everything:** `'crawler'` for all njit.edu content → re-crawl/reconcile own it;
  never clobbers `'dashboard'` data.
- **Never insert `search_text`** (generated column) — `upsert_doc_items` already respects this.
- **Honest-partial / extractive:** spans must appear literally on the page; the ground-filter
  drops the rest. No fabricated permit prices, phone numbers, or hours.
- **Fetch failures** (`http_fetch` status != ok) are skipped with a logged SKIP line, not faked.

## Testing
- **TDD unit tests** for `select_links` (the new staging helper): protocol-relative + relative +
  absolute normalisation; same-host filter (drops external hosts); `--follow` match/no-match;
  asset/query/fragment drop; dedupe. Fixture HTML modelled on the real hub.
- **`_crawl_ingest` per-doc org:** test that a doc with `org: eos` files under the eos org id and
  a doc without `org:` falls back to njit; missing-org slug raises.
- **Dev-copy integration:** run the full chain against `/tmp/dev.db`, then `verify_kg`-style spot
  checks (org exists, `part_of njit`, KB rows present, source='crawler').
- **Chat verification** (live, after embed): "where do I park", "how much is a parking permit",
  "visitor parking", "campus shuttle / transportation", "how do I get my photo ID",
  "who do I call for a lockout", "where's the mailroom", "what is EOS".
- **Eval suite grows:** add the above to `eval/questions.txt` under a `# parking / operations`
  header (per the "grow correctness suite" rule), **plus adversarial routing probes** that would
  catch the mis-route class if aliases ever return: "who works in parking", "parking staff",
  "parking leadership", "who is the parking director", "parking office phone number", "EOS contact".

## Out of scope / deferred (loudly flagged)
- **No Person nodes / roles** for EOS staff — captured as KB contact prose only (matches the
  org-modeling decision). If we later want "who is the parking director", that's a separate
  decision.
- **No scheduled re-crawl wiring** for the EOS seed set in this pass — the seed/follow mode is
  reusable and re-runnable by hand; folding it into a dashboard "Data Sources" job is a
  follow-up, not built here.
- **SchoolDude / EHS / Sustainability** sub-pages are ingested as prose if linked from the hub
  and they fetch cleanly; if any is a JS-only app or off-host portal that `clean_text` can't
  render, it is **skipped and flagged**, not faked.

## Goals checklist (to verify at sign-off — shipped vs deferred)
- [ ] EOS Org node created (`office`, `part_of njit`), resolvable by "EOS" + full name (NO aliases) — SHIP
- [ ] Service areas as grounded KB prose under the eos org (per-page, service-named titles) — SHIP
- [ ] `_crawl_stage.py` seed/link-follow mode (reuses web_crawler primitives, robots-aware, tested) — SHIP
- [ ] `_crawl_ingest.py` per-doc org support (local `_read_org`, move-safe) — SHIP
- [ ] Operations heads-up topic + source_url verify-link surfaced — SHIP
- [ ] Embed + 8 chat verifications + adversarial routing probes pass — SHIP
- [ ] Eval questions added (incl. adversarial probes) — SHIP
- [ ] Person nodes for EOS staff — DEFER (KB contact prose instead)
- [ ] Scheduled/dashboard re-crawl job for EOS — DEFER. **Retrieval consequence (review S3):** a
      yearly permit-fee change needs a manual re-run until this lands; mitigated by the source link
      + operations heads-up so users can self-verify a possibly-stale KB price.
```

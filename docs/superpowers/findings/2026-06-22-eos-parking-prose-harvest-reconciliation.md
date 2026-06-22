# Parking/EOS ↔ Prose-Harvest reconciliation — parking = entry-point #1

**Date:** 2026-06-22
**Purpose:** Reconcile the ad-hoc parking/EOS design (`2026-06-22-eos-parking-knowledge-design.md`,
this branch) with the systematic architecture (`2026-06-22-njit-prose-harvest-design.md`, `main`
`d35fa69` / `fa9538c`). The prose-harvest spec is the ARCHITECTURE; parking is the concrete first
`aspect="office"` instance. Direct input for the prose-harvest build session.
**Confirmed with owner 2026-06-22.**

## Confirmed decision — EOS modeling for entry-point #1

EOS is modeled as **multiple `aspect="office"` entry-point rows sharing `org_slug='eos'`**, NOT one
entry point. Seeds for the EOS cluster:
- `https://www.njit.edu/parking/` (the hub — Drupal multisite `njit.edu.parking`)
- `https://www.njit.edu/mailroom/`
- `https://www.njit.edu/sustainability/`
- `https://www.njit.edu/environmentalsafety`
- `https://www.njit.edu/about/transportation-campus`

All file prose under the single `eos` Org node (`type='office'`, parent `njit`). Cross-prefix links
discovered from the `/parking/` hub are registered as `status='candidate'` rows via self-extension
(§4.6) and activated (gated) into the cluster.

## ⚠️ Architecture finding — prose-harvest §4.2 same-scope rule misses EOS

§4.2 bounds an office crawl with `same_scope(seed,url)` = same-host **AND**
`path.startswith(scope_prefix(seed))`. **EOS breaks this:** it is a separate multisite whose service
areas live on the SAME host (`www.njit.edu`) but under DIFFERENT path prefixes (`/mailroom/`,
`/sustainability/`, `/environmentalsafety`, `/about/transportation-campus`). A single `/parking/`
entry point (`scope_prefix='/parking/'`) would **silently miss every cross-prefix service area** — a
coverage defect, not a crash.

**Recommended fix (no schema change):** the multi-entry-point modeling above + self-extension
candidate rows for the discovered cross-prefix hubs. (Alternative — widen an entry point to carry
multiple scope prefixes — is more schema and less clean.) The build session should treat EOS as the
worked example proving the registry handles a multisite office whose footprint spans prefixes.

## Reconciliation map

### Carry forward (parking decisions that fit the architecture as-is)
- ONE `eos` Org node; service areas = KB topics, not child orgs; contacts/locations as prose, no
  Person nodes (honest-partial).
- **NO org aliases** — and WHY: an alias like "parking" resolves `org_id=eos`, then
  `router.py` fires `people_in_org(eos)` on a staff/leadership cue → empty → a confident
  "I don't have people listed" deflection that does NOT fall through to RAG. Office prose lives in a
  fallback tier answered by RAG, so no alias is needed. **Adopt as a general rule for all office orgs.**
- `operations` heads-up topic (built + tested this branch) — the `apply_headsup` safety layer §4.3
  calls for on the ungrounded chunk leg. Drop-in reusable.
- Extract-only for volatile facts (permit FEES, hours, lockout #) — aligns with **RA4**.
- Invariants identical: `source='crawler'`, generated `search_text`, gated writes, dev-copy-first.

### Must change (to fit the architecture)
1. **Ingest target:** parking prose → **`item_type='office_page'`** (in `DEFAULT_EXCLUDE_TYPES`,
   answered by the separate office tier on primary-miss, gated by `OFFICE_THRESHOLD`), NOT
   `doc_type='reference'` in the primary corpus. This is the single biggest change — it's the
   dilution guard (§4.4, SE2/RA1–RA3).
2. **Crawl driver:** run through `web_crawler.crawl_site` + the new `aspect="office"` link policy
   (follow all same-scope HTML links, per-entry budget/depth), NOT the bespoke `_crawl_stage
   --seed/--follow` depth-1 mode.
3. **Recurrence:** parking is NO LONGER deferred — it becomes registry rows with `crawl_interval_days`
   under the change-detected re-crawl job (D4, §4.5) with the 404/410 retire guard (SE3).

### Superseded vs reusable code (this branch, `99c9a84`)
- **Superseded:** `scripts/_crawl_stage.py` `--seed/--follow` + `select_seed_links` (replaced by
  `crawl_site` + office link policy). Also: SE3 requires `make_fetcher` to **surface HTTP status**;
  the branch used it as-is (None on all errors) — the build must add status-surfacing.
- **Reusable now:**
  - The ~40 discovered EOS URLs (see `2026-06-22-eos-parking-crawl-notes.md`).
  - The **`.css?delta=` asset-drop** insight: `.css/.js/.ico` are NOT in `web_crawler._NON_HTML_EXT`;
    the office link policy must apply that extra asset filter.
  - The **empty-body/JS-only-shell skip** (SchoolDude work-order, parking-availability/visitor apps)
    → feeds the RA5 pre-ingest quality gate.
  - The `bot/core/headsup.py` `operations` topic + its tests.
  - The org-modeling + no-aliases decisions above.

## Build order implication
Parking-done-right depends on the systematic primitives (registry table, office link policy,
`office_page` type + office tier, status-surfacing fetcher, precedence ladder). It is built
**with/after** those, then exercised as the first `aspect="office"` rows (the EOS cluster) and
validated by the parking chat-Qs + the adversarial routing probes already drafted in the parking
spec's Testing section + `eval/questions.txt`.

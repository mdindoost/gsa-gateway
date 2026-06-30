# Scholar Fetcher — Maximal Capture into the KG

**Date:** 2026-06-29
**Author:** Claude (brainstormed with Mohammad)
**Status:** Design — pending expert review + owner approval (HARD GATE)
**Related:** [[project_scholar_fetcher_gaps]], [[project_faculty_page_builder]] (Folio),
`docs/superpowers/specs/2026-06-19-person-external-profiles-design.md`

---

## 1. Goal

When we fetch a faculty member's Google Scholar profile, capture **as much structured
information as the public profile page exposes** — regardless of whether the bot or Folio uses
it today — and keep all of it **in the KG** (the graph layer: the `Person` node's `attrs`).

This extends the existing Scholar refresh (`v2/core/ingestion/scholar.py`), which today captures
only all-time metrics (citations/h-index/i10) + research interests. We widen it to the full
boundary below, in one pass.

## 2. Reframing the original two gaps

The project memory framed two gaps. This design **resolves both, but not as originally written**:

- **GAP 1 (metric history) — KEPT, as an append-only snapshot list in the KG.** Each refresh
  appends a `{date, metrics…}` snapshot to `attrs.profiles.scholar.history`, so we can compute
  refresh-to-refresh deltas the annual chart can't (e.g. "+47 citations since last month," momentum
  between *our* pulls). The annual chart (`#gsc_g_bars`) is *also* kept (year-granular history in
  one fetch) — the two are complementary: chart = annual, history = per-refresh.
  - **We store RAW snapshots only.** Any *derived* metric — delta, per-month rate, growth %,
    momentum, "fastest-growing" — is computed **at read time** by the consumer (bot/Folio), never
    stored. Storing raw facts and deriving views on demand means the formula can change anytime
    with no re-fetch and nothing goes stale. So delta *definitions are out of scope for this build*
    — this build only captures + appends the snapshots.
  - Storage is **Option A: a list inside the Person node's `attrs`** (KG-native, consistent with the
    rest of the capture; tiny — a monthly refresh ≈ 12 entries/year). NOT a satellite table
    (rejected: keeps everything in the KG; cross-person history analytics, the only thing a table
    would buy, is not a near-term need).
- **GAP 2 (publications) — DELIVERED, KG-native.** Capture the publications the profile exposes
  (with per-paper cited-by) as **bounded highlight lists on the Person node**, plus the trend
  chart, recent-metrics column, co-authors, and profile scalars.

## 3. Scope decisions (locked with owner)

- Store everything in the **KG** = `nodes.attrs` (NOT the `knowledge_items` KB text layer; that is
  where the stale crawler pub rows live and it is excluded from answers anyway).
- Keep **bounded highlights**, not the full paper corpus.
- **No `.bib` / full-corpus persistence.** (Explicitly dropped.)
- **No metric-history table.** (Chart replaces it.)
- `cited_by` is required and is reliably grabbable (validated on 3 real profiles) — it stays.

## 4. Data model — `Person.attrs.profiles.scholar`

All fields below live on the existing per-person `attrs.profiles.scholar` bag, written via
`people_editor.set_person_profiles` (deep-merge; keeps `url`). Bounded set ≈ 20 paper records +
≤20 co-authors + a ~30-entry chart map ≈ a few KB of JSON per person.

| Field | Content |
|---|---|
| `url` | Scholar profile URL (unchanged) |
| `citations`, `h_index`, `i10_index` | all-time metrics (the "All" column) |
| `recent_citations`, `recent_h_index`, `recent_i10_index` | the "Since YYYY" column |
| `recent_since_year` | the YYYY label of the recent column (e.g. `2021`) |
| `cites_per_year` | `{ "YYYY": int, ... }` — full per-year citation chart map |
| `top_cited` | **5** most-cited papers (all-time); `[]` if none |
| `newest` | **5** newest papers (year-descending); `[]` for a 0-publication profile |
| `current_year` | current-year papers **visible on the first pubdate page**, ≤10 (10 most-cited of those if more); `[]` when none |
| `coauthors` | **all** co-authors shown (≤20): `{name, affiliation, url}`; `[]` if none |
| `history` | **append-only** list of per-refresh snapshots (see below). Never replaced; grows by one entry per refresh date. |
| `photo` | profile photo URL (or `null`) |
| `homepage` | the profile's homepage link (or `null`) |
| `affiliation` | the affiliation/title string (or `null`) |
| `public_access` | `{available: int, not_available: int}` — **`null` when the block is absent** (0/0 would falsely read as "real zero") |
| `updated_at` | full ISO date (`YYYY-MM-DD`) of this refresh |

**`current_year` is bounded by the first pubdate page (~20 rows).** We do not page further: a
profile with >20 papers in a single calendar year is vanishingly rare, and the spec must not
promise more than one page delivers. The field is honestly "current-year papers among the newest
page," capped at 10 most-cited.

**Recent-metric fields may be absent.** A profile whose stats table has no "Since YYYY" column
yields `recent_*` and `recent_since_year` = `null` (the parser reads the header defensively, not by
fixed column index).

**History snapshot** (one entry of `history`):
`{ date, citations, h_index, i10_index, recent_citations, recent_h_index, recent_i10_index }`
— `date` is the refresh ISO date; all six metrics are banked (free, and maximizes what can be
derived on the fly). A `null` metric (e.g. missing recent column) is stored as `null` in the
snapshot, not dropped — so the time-series stays date-aligned.

**Paper record** (each entry of `top_cited` / `newest` / `current_year`):
`{ title, year, venue, authors, cited_by, url }`
— `authors`/`venue` are the abbreviated/possibly-truncated strings the list view gives;
`cited_by` is an int (0 for uncited); `url` is the citation-detail link (doubles as a stable
per-paper id via its `citation_for_view` cluster token).

Minor overlap across the three lists (a paper that is both newest and most-cited) is acceptable —
they are three views, and de-duping consumer-side is trivial.

**Research interests** continue to flow to `ResearchArea` nodes + `researches` edges via
`set_person_research_areas` (existing behavior — unchanged).

## 5. Fetch strategy — 2 light fetches, merge

The bounded highlights require two orderings; each needs only the **default first page (~20 rows)**
— NOT a full-corpus `pagesize=100` pull.

| # | Request | Yields |
|---|---|---|
| 1 | default profile page (`?user=<id>&hl=en`) | metrics, `cites_per_year`, `coauthors`, profile scalars, **`top_cited`** (page is citation-ordered) |
| 2 | `&sortby=pubdate` | **`newest`**, **`current_year`** (a new 0-cite paper sits at the bottom of the cited order, so date-order is required) |

**Merge key = the `citation_for_view` cluster token** (`...&citation_for_view=<USER>:<CLUSTER>`),
NOT the raw href — the two pages' URLs differ in `hl`/`oe`/param order, so merging on the cluster
token is required to avoid duplicate/mismatched rows. Title+year is the fallback id for a malformed
row missing the token. For small profiles one fetch would suffice, but always doing two is harmless
and keeps one code path. Politeness delay between fetches retained (`delay`, default 3.0s).

**Atomic write policy (both fetches are one unit).** If **either** fetch fails (non-`ok` status)
or the first page fails to parse metrics, **skip the person — no write at all.** This matches
today's all-or-nothing behavior and, critically, never overwrites good existing highlights with
empties on a transient failure. On **full success**, the `scholar` bag is written as a **complete
snapshot**: every volatile field above is emitted (empty `[]`/`null` when genuinely absent),
so a previously-populated field never goes stale. `url` (and any non-Scholar profile keys) are
preserved by the per-field deep-merge of `set_person_profiles`.

**`history` is the ONE append-only exception.** `set_person_profiles` merges the `scholar` bag with
a dict `.update()`, which *replaces* a list value by key — so naively passing `history=[today]`
would **wipe all prior snapshots**. Therefore `refresh_scholar` must **read the person's existing
`attrs.profiles.scholar.history`, append today's snapshot (de-dup by `date` — a same-day re-run
overwrites that day's entry), and pass the COMPLETE rebuilt list.** The `.update()` then swaps
old-full-list → new-full-list = a correct append. This read-modify-write lives in the orchestrator
(not in `set_person_profiles`, which stays a generic merge) and is covered by an explicit test:
*two refreshes on different dates → two history entries, not one.*

**Validated selectors** (plain urllib + bot UA → HTTP 200, no CAPTCHA, on 3 real profiles
2026-06-29):

- name `#gsc_prf_in` · affiliation `.gsc_prf_il` · photo `#gsc_prf_pup-img` · homepage `#gsc_prf_ivh a`
- interests `#gsc_prf_int a`
- metrics `#gsc_rsb_st tr` (col 2 = All, col 3 = Since-YYYY; header row col 3 = the label)
- chart years `.gsc_g_t`, values `.gsc_g_al`
- pubs `tr.gsc_a_tr` → title/link `.gsc_a_at` · authors+venue `.gs_gray` (2 spans) · year `.gsc_a_y span` · cited-by `.gsc_a_c a`
- co-authors `#gsc_rsb_co .gsc_rsb_aa` → name `.gsc_rsb_a_desc a` · affiliation `.gsc_rsb_a_ext`
- public access `#gsc_rsb_mnd`

## 6. Honest all-time-peak guard (anti-fabrication)

The per-year chart is a **window** (e.g. Bader's starts 1996, Koutis's 2007), so it can omit
pre-window citations. Any "peak / all-time-most-cited year" claim MUST be guarded:

> `hidden = max(0, citations - sum(cites_per_year.values()))` (clamp — a non-negative remainder).
> Only call the max chart year an **all-time** peak when `peak_value > hidden`.
> If `citations` or `cites_per_year` is missing/empty, **make no all-time claim at all.**
> Otherwise the honest phrasing is "peak in the last N years" / "most-cited year since <window-start>".

This is a **read-time** rule (computed by whatever surfaces the claim — bot or Folio). We store the
raw `cites_per_year` + `citations`; we do not store a derived boolean. This honors the hard line on
never overstating an unprovable claim.

## 7. Storage & invariants

- Writes via `set_person_profiles(conn, person_key=…, profiles={"scholar": {...}})` — deep-merge,
  metric strings coerced to ints, caller owns the txn. New list/dict fields (`cites_per_year`,
  `top_cited`, …) pass through the generic bag unchanged.
- **Provider isolation** preserved: parsing is provider-agnostic; HTTP is the injected `fetch`.
  `default_fetch` stays best-effort urllib; Scholar blocks bots at volume, so scaling to all
  ~1,200 faculty still needs a sanctioned provider (SerpAPI) — **owner-deferred**. CS-50 pilot via
  polite manual/WebFetch is fine.
- **Gated writes:** the `scripts/refresh_scholar.py` runner stays dry-run by default, takes a
  `hardened_backup`, requires `--commit` (unchanged).
- **Anti-block (volume run):** `refresh_scholar` accepts `jitter=(lo,hi)` (random lo..hi s between
  people, overrides fixed `delay`), `fetch_gap` (seconds between a person's 2 fetches), and
  `block_abort` (stop after N CONSECUTIVE blocked/failed people; a clean person resets the counter)
  — mirrors the proven `scholar_discovery.run_sweep` machinery. `sleep`/`rand` are injectable for
  tests. CLI flags: `--jitter-min/--jitter-max/--fetch-gap/--block-abort`. Stats gain an `aborted`
  flag; the CLI prints a resume hint (`--older-than 1`) on an early abort. A whole-NJIT run (211
  people w/ a URL, 422 fetches) at jitter 60–120s + 4s gap ≈ ~6h; pilot a college first.
- DB target = `gsa_gateway.db` (the KG/knowledge DB), not `gsa_gateway_ops.db`.
- **These fields are denormalized profile snapshots, not canonical graph entities.** `top_cited`/
  `newest`/`current_year`/`coauthors` are display-oriented highlights captured at `updated_at`;
  they are not `Publication`/`Coauthor` nodes and consumers should treat them as stale after
  `updated_at`. (Promotion to real nodes/edges is the deferred path in §9.)

## 8. Parsing functions (in `v2/core/ingestion/scholar.py`)

New/updated pure parsers (provider-agnostic, each independently testable on saved HTML):

- `parse_scholar_metrics(html)` → widen to also return the recent column + `recent_since_year`.
- `parse_cites_per_year(html)` → `{year:int}`.
- `parse_scholar_publications(html)` → `[paper, …]` from `tr.gsc_a_tr`.
- `parse_scholar_coauthors(html)` → `[{name, affiliation, url}, …]`.
- `parse_scholar_profile(html)` → `{photo, homepage, affiliation, public_access}`.
- `derive_highlights(cited_pubs, date_pubs, today)` → `{top_cited, newest, current_year}` (pure;
  the merge + sort + cap logic, unit-testable without network).
- `refresh_scholar(...)` orchestrates: fetch×2 → parse → **read existing `history`, append today's
  snapshot (de-dup by date)** → assemble the complete `scholar` bag → `set_person_profiles`
  (+ interests → `set_person_research_areas`, unchanged).

## 9. Out of scope (explicitly deferred)

- **Per-paper deep fetch** (full author list, full venue, abstract, clean BibTeX) — one fetch per
  paper; not needed for highlights.
- **Co-author graph** (promoting the `coauthors` list to `Person` nodes + `coauthor` edges) — kept
  as a list now; promotable later with no data loss when Find-My-Advisor needs it.
- **Full-corpus / `.bib` persistence** — dropped.
- **Derived history metrics** (deltas, per-month rate, momentum, growth %) — NOT stored; computed
  at read time by the consumer. This build only captures + appends raw snapshots; the delta
  formulas are a later, read-time decision.
- **Satellite history table** — rejected in favor of the in-attrs list (Option A).
- **Scaled provider (SerpAPI)** for all-faculty coverage — owner-deferred.

## 10. Testing (TDD)

Unit tests against **saved HTML fixtures** of the 3 real profiles (Dindoost / Koutis / Bader),
covering: recent-column parse, chart parse + hidden-citation reconciliation, the 3 highlight lists
(incl. empty `current_year`, >100-paper Bader needing the merge, 0-cite paper → `cited_by=0`),
co-author parse, profile scalars, and the peak-guard helper. `refresh_scholar` tested with an
injected fake `fetch` returning the fixtures (no network in tests). **History append test:** two
refreshes on different `today` dates → `history` has two date-ordered entries (not one); a same-day
re-run → one entry (de-dup), never wiped. **Atomic-write test:** a failed second fetch → person
skipped, prior data intact (no partial write).

## 11. Goals checklist (shipped / deferred) — per the review-against-plan rule

| Goal | Status |
|---|---|
| Recent ("Since YYYY") metrics | ship |
| Per-year citation chart | ship |
| `top_cited` (5) | ship |
| `newest` (5) | ship |
| `current_year` (≤10) | ship |
| Co-author list (name/affiliation/url) | ship |
| Profile scalars (photo/homepage/affiliation/public_access) | ship |
| Honest all-time-peak guard (read-time rule) | ship |
| 2-fetch strategy, no full-corpus pull | ship |
| Refresh-to-refresh metric history (append-only `history` list, raw snapshots) | ship |
| Derived history metrics (deltas/momentum) | read-time, not stored (out of scope) |
| Per-paper deep fetch / BibTeX | deferred |
| Co-author graph (nodes/edges) | deferred |
| `.bib` / full-corpus persistence | DROPPED |
| SerpAPI scaled provider | owner-deferred |

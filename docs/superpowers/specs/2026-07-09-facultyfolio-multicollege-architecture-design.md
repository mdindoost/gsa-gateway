# FacultyFolio — Multi-College Architecture + Scoped Builds (Spec A)

**Date:** 2026-07-09
**Status:** DESIGN — reviewed + changes folded. Fable: SHIP-WITH-CHANGES (sign-off given on the
`SITE_ORIGIN` fix). Senior-eng: APPROVE-WITH-CHANGES (approve to plan once the three blocking items
are written in). Owner delegated Spec A sign-off to Fable. → Ready for the implementation plan.
**Author:** brainstormed with owner (Mohammad); architecture opinion + gating review by Fable;
parallel senior-eng design review. Both reviews' required changes are folded below (see §15 changelog).

> This is **Spec A** of a two-spec "fix the design before expanding" effort. Spec B (page/visual
> design pass) is separate and comes after. This spec restructures the generator so it can serve
> ALL NJIT colleges and can be run scoped to one college or one department — but the only college
> actually built and tested here is **YWCC** (the current live set). Publishing a new college is a
> deliberate registry flip that happens *after* this ships.

---

## 1. Problem / motivation

The generator has grown CS → DS + Informatics → YWCC college hub, and the owner now wants to expand
to every NJIT college. Before scaling, three design gaps must be fixed:

1. **Single-college hardwiring.** `build.py::build_all()` is bound to one college via
   `config.COLLEGE_SLUG = "ywcc"`: it finds the YWCC node, auto-discovers its departments, builds
   every dept's profiles + a 3-view leaderboard, and writes ONE hub at root `index.html`. A second
   college has nowhere to live — two hubs would both want `/index.html`.
2. **No scoped runs.** You cannot build "just CS." The level-builders (`build_one`, `build_dept`,
   `build_hub`) exist standalone, but nothing exposes a scoped, ancestor-consistent run. Every change
   forces a full rebuild.
3. **Flat single-college URL layout.** Root `/` = the one college hub; `/<dept>/` = leaderboards.
   There is no NJIT-level landing and no college level in the path.

Fixing these is the prerequisite that unblocks multi-college expansion.

## 2. Goals

- G1. A three-level site that mirrors the KG: **NJIT hub → college hub → department leaderboard**,
  with flat profile pages.
- G2. An **explicit published-colleges registry** — a college goes live only when its slug is added,
  after the owner has eyeballed it. Departments are still auto-discovered per college.
- G3. **Scoped/incremental builds**: run the whole site, one college, or one department, from a CLI.
- G4. **Scoped-write safety + consistency**: a scoped run regenerates its own pages AND the ancestor
  hubs (so counts never drift), but never writes any sibling college/department's files and never
  wipes a directory.
- G5. **SEO continuity across the URL change**: `sitemap.xml`, `robots.txt`, and a `<link rel=canonical>`
  on every page, plus redirect stubs for the moved leaderboard URLs.
- G6. Restructure the **current YWCC** site into the new tree with all of the above working and tested.
  No behavior change to page *content* (that's Spec B).

## 3. Non-goals (explicitly deferred / out of scope)

- **Actually publishing MTSM / NCE / CSLA / HCAD / Honors.** Only the discovery/hub *seams* that make
  them slot in are designed here; only YWCC is built and tested. Publishing each is a later registry flip.
- **Page/visual design changes** — Spec B (shared templates/CSS; propagates to all colleges for free).
- **Stale-file pruning** — additive-never-wipe leaves orphaned files (renamed dept slug, departed
  faculty). Documented as a known manual op with a future `--manifest-diff --prune`; not built here.
- **Profile URL rename.** Profiles stay at `/p/<slug>.html` (owner: zero churn on 119 live URLs).
- **Cross-college global search / aggregated college-level leaderboards** (beyond a dept-less college's
  own leaderboard) — defer until a second college is actually live.
- **Any JS framework / TypeScript / micro-lib.** Stack stays static Python + Jinja + the existing
  ~40 lines of vanilla JS (§10).

## 4. Decisions locked (owner + Fable)

| # | Decision | Choice |
|---|----------|--------|
| D1 | URL hierarchy | Nested hubs (`/<college>/<dept>/`) + flat profiles (`/p/<slug>.html`) |
| D2 | Profile URL | **Keep `/p/<slug>.html`** — no rename, no `/p/` redirects |
| D3 | College registry | **Explicit** `PUBLISHED_COLLEGES` list; depts auto-discovered per college |
| D4 | Scoped writes | Refresh scope **+ ancestor hubs**; never write siblings; never wipe |
| D5 | NJIT hub | Show **only published** colleges (no "coming soon" placeholders) |
| D6 | SEO | `sitemap.xml` + `robots.txt` + per-page canonical **in this spec** |
| D7 | Tech stack | **Static Python/Jinja, no framework** (Fable-confirmed) |
| D8 | Dept-less college (MTSM) | Its leaderboard renders **at** `/<college>/` (no empty one-card hub) — seam only |
| D9 | Child discovery | **Type-agnostic**: any child org with faculty>0. NOTE: `db.dept_orgs_of_college` is *already* type-agnostic + faculty>0-gated (`db.py`), so HCAD "schools" already resolve today — the only add-time work is the hub heading label (§14) |

## 5. Site structure

```
/                          NJIT hub  — one card per PUBLISHED college (D5). Root changes
                                       from the YWCC hub to the NJIT hub.
/<college>/                college hub — one card per child org (dept or school) with faculty>0.
                                       e.g. /ywcc/
/<college>/<dept>/         department leaderboard (the existing 3-view + Rising leaderboard).
                                       e.g. /ywcc/computer-science/
/p/<slug>.html             faculty profile — flat, globally-unique slug, UNCHANGED.
/assets/photos/<slug>.jpg  photo — flat, keyed by slug (unchanged) + a build-time uniqueness assert.
/assets/…                  css / fonts (unchanged).
/sitemap.xml               all emitted canonical URLs.
/robots.txt                allow all + Sitemap: line.
```

**Slug safety:** verified this session — 959 person profiles, all slugs globally unique, zero
collisions; dept slugs likewise unique. So the flat profile namespace and flat photo namespace are
safe NJIT-wide. A **build-time assertion** fails loudly if two published faculty ever resolve to the
same slug (profiles and photos both depend on it), rather than silently overwriting.

**Redirect stubs** (meta-refresh + canonical — GitHub Pages cannot issue 301s; stubs are **permanent**).
`LEGACY_REDIRECTS` maps `old_segment → target_path`; the **target is stored as a segment path with no
leading slash and no origin** (`"ywcc/computer-science"`), because `_redirect_html` composes it as
`../{target}/index.html` relative to `/{old}/index.html` (C3):
- `computer-science` → `ywcc/computer-science`
- `data-science` → `ywcc/data-science`
- `informatics` → `ywcc/informatics`
- `cs` → `ywcc/computer-science` (updates the existing stub; was `cs`→`computer-science`)

No `/p/` redirects (D2). The NJIT hub links straight to each college, so the moved root is not a dead end.

**Redirect clobber guard (C-1, a real fix — not a rename):** the current guard is
`if old in dept_slugs: continue` ("don't clobber a live dept dir"). In the new tree the legacy segments
(`computer-science`, …) are **equal to the dept slugs**, so that guard would suppress exactly the
redirects listed above. Redefine the guard to key on **output-path occupancy**, not name membership:
write a stub at `/<old>/index.html` only if **no real page already occupies that exact root-level path**
(the live dept now lives at `/ywcc/computer-science/`, so `/computer-science/` is free; a published
college hub like `/ywcc/` is occupied and must never be stubbed over). Invariant lives in §8.

## 6. Config generalization (`config.py`)

- Replace the single `COLLEGE_SLUG = "ywcc"` with a registry:
  ```python
  PUBLISHED_COLLEGES = ["ywcc"]   # ordered; build/iteration order is registry order (deterministic)
  ```
- `COLLEGE_NAMES` (full display names) and `LEGACY_REDIRECTS` already exist as maps — extended for
  the new leaderboard redirects. `CS_ORG_ID` / `KOUTIS_NODE` remain as named test anchors only.
- **New: `SITE_ORIGIN`** (C1 — the one item all of G5 depends on). An absolute origin, e.g.
  `SITE_ORIGIN = "https://facultyfolio.github.io"`. Every canonical `<link>`, every `sitemap.xml`
  entry, and the `robots.txt` `Sitemap:` line is built as `SITE_ORIGIN + <absolute path>`. Without it,
  `sitemap.xml`/robots would carry relative URLs (invalid) and canonicals would be near-useless for SEO.
  A page's absolute path is derived from the same `paths.py` seam that writes it (single source of truth).
- Everything else about a college (its depts, names, counts) is **derived from the KG**, not config.

## 7. Build orchestration + CLI (`build.py`)

**Approach (Fable-endorsed): extend the existing level-builders — no object-graph rewrite.** NJIT is
exactly three levels (university → college → dept/school) and will not grow a fourth; three named
builders are the honest model.

New/changed builders:
- `build_one(slug, out_root, photo_ref=None)` — profile. **Unchanged** (path stays `/p/<slug>.html`).
- `build_dept(org, out_root, photo_map)` — dept leaderboard. Path becomes `<college>/<dept>/index.html`.
- `build_college_hub(college, depts, out_root)` — **new**. Card per child org (dept/school) with
  faculty>0. For a dept-less college (D8) this renders the college leaderboard instead of an empty hub.
- `build_njit_hub(published_colleges, out_root)` — **new**. Card per published college; each card shows
  the college's **subtree-distinct** coverage (see the coverage helper below).

**`paths.py` changes (the URL seam — the whole reason this module exists; C2):**
- `leaderboard_path(out_root, college_seg, dept_seg)` — gains the college segment → `<college>/<dept>/index.html`.
- **new** `college_hub_path(out_root, college_seg)` → `<college>/index.html`.
- **new** `njit_hub_path(out_root)` → `index.html`.
- **new** `sitemap_path(out_root)` / `robots_path(out_root)`.
- `redirect_path` and canonical-URL building updated in lockstep with the nested layout.
- `profile_path` (`/p/<slug>.html`) and `assets_dir` — **unchanged**.

**College-aggregate coverage helper (S-2 — closes the G1/G2 hand-wave):** the NJIT-hub cards and any
college total need **distinct home faculty across the college subtree**, NOT a sum of dept coverages
(a dept-sum double-counts the ~12 known dup-home faculty and ignores faculty homed directly on the
college node). Add `db.college_coverage(college_node) -> (n_with_scholar, m_total)` that counts
DISTINCT home-faculty person ids across the college and all its faculty>0 child orgs (GROUP/DISTINCT
by person id, mirroring how `top_people_by_metric` de-dups multi-role people). Dept-level
`rank.coverage(org_id)` is unchanged and still used for dept cards on the college hub.

**Template sharing (C5 — decided at build time, not here):** `build_njit_hub` (cards→colleges) and
`build_college_hub` (cards→depts) are the same card-grid shape; whether they share one parameterized
`hub.html` or split is a build-time call, not a spec decision.

**Scope-aware orchestrator + argparse CLI:**
```
python -m facultyfolio.build                        # default: all published colleges
python -m facultyfolio.build --college ywcc         # one college: its depts + college hub + NJIT hub
python -m facultyfolio.build --dept computer-science# one dept: profiles + dept leaderboard + ancestor hubs
```
- `--dept` resolves its parent college from the KG (`part_of`); errors if the parent college is not
  in `PUBLISHED_COLLEGES`.
- `--college` / `--dept` error if the slug is unknown or unpublished.

## 8. Scoped-write semantics (D4 — the core safety contract)

A scoped run writes exactly:
1. the pages **in scope** (dept: its profiles + its leaderboard; college: all its depts' profiles +
   leaderboards + the college hub), **and**
2. the **ancestor hubs** — the parent college hub and the NJIT hub — regenerated with fresh counts.

Invariants:
- **Never writes a sibling's file.** Building CS does not touch DS's or Informatics's profiles/
  leaderboards, nor another college's pages.
- **Never wipes a directory.** All writes are targeted file writes; the tree is only added to /
  overwritten in place.
- **Reads ≠ writes.** Regenerating the NJIT hub *reads* every published college's counts (cheap
  queries) but only *writes* the two hub files. This is correct and intended — noted so nobody later
  "optimizes" it into staleness.
- **Deterministic order + loud dup-home.** College iteration follows registry order; the existing
  cross-dept dup-home WARN in `build_all` now tie-breaks by (registry order, then dept order) and
  stays loud. Known producer issue: ~12 multi-home faculty revert on re-crawl, so this WARN WILL fire
  after a re-crawl until the producer durability fix lands — expected, not a failure.
- **Redirect stubs never clobber a real page (C-1).** A legacy stub is written at `/<old>/index.html`
  only if no real page occupies that exact root-level path (output-path occupancy, not name membership
  — see §5). A published college hub (`/ywcc/`) and any live dept dir are occupied and protected.
- **Flat-namespace sibling-safety depends on global slug uniqueness (S-1).** D4's "never write a
  sibling's file" is guaranteed *by construction* for the hub/leaderboard directory tree. For the
  **shared** flat namespaces (`/p/<slug>.html`, `/assets/photos/<slug>.jpg`) it holds only because
  slugs are globally unique. A scoped build enumerates only its own scope, so it **cannot** detect a
  cross-scope slug collision — the build-time uniqueness assert (§5) is meaningful **only on a full
  build**, which is where it runs and fails loudly. Scoped builds trust the invariant the full build
  enforces. (Verified safe today: 959 slugs, zero collisions.)

## 9. SEO files (D6)

All three artifacts use **absolute** URLs built as `config.SITE_ORIGIN + <path>` (§6, C1):
- `sitemap.xml` — every emitted canonical URL (NJIT hub, each college hub, each dept leaderboard, each
  profile). Regenerated on a full build; on a scoped build it is **regenerated over the full published
  set from the KG**, not just the scope (this is how a scoped build avoids silently shrinking the
  sitemap — asserted in §11).
- `robots.txt` — allow all; `Sitemap: {SITE_ORIGIN}/sitemap.xml`.
- `<link rel="canonical">` on every page (profiles, leaderboards, hubs) = `SITE_ORIGIN + own path`.
  Redirect stubs carry a canonical to their target.

## 10. Technology (D7)

No change. Static Python + Jinja → static HTML on GitHub Pages. Interactivity stays the existing
~40 lines of vanilla JS (leaderboard view-toggle + client-side search) + build-time SVG charts.
Rationale (Fable): a public, SEO-relevant, read-only document site of ~1,000 pages is exactly what
static HTML is for; a JS/TS framework would force a Python→JSON bridge and a second toolchain for zero
user-visible gain; scale (959 pages in seconds) forces nothing. The only future trigger to revisit is a
genuinely app-like feature (cross-NJIT faceted search, claim-flow) — and even then the answer is a
single interactive *island* fed by a build-time JSON index, not a stack migration. Explicitly rejected
as gold-plating: Alpine/any micro-lib, a generic site-tree abstraction, hash-based incremental rebuild.

## 11. Testing

- **Per-level unit tests** — `build_one`/`build_dept`/`build_college_hub`/`build_njit_hub` each write
  the expected path with the expected content, against the live DB (self-relative assertions, no
  hardcoded counts — the byte-stability lesson from prior work).
- **Scoped-write manifest test** — build `--dept computer-science` into a tmp `out_root`; assert the
  set of written files == the expected manifest (CS profiles + CS leaderboard + YWCC hub + NJIT hub +
  sitemap/robots), AND pre-seed a sentinel file in a sibling dept's dir and assert its bytes are
  untouched. Cheap, precise, no golden-HTML brittleness.
- **Ancestor counts actually refresh (S-4)** — not just that the hub files are *written*: assert that
  when the underlying roster/coverage changes, a scoped `--dept` build's rendered NJIT/college hub
  reflects the fresh count (the whole point of D4 is "counts never drift" — test the count, not the file).
- **Scoped sitemap stays full (S-4)** — assert a scoped `--dept` build's `sitemap.xml` still lists
  out-of-scope colleges/depts, guarding against a silent regression to scope-only.
- **Redirect-stub coverage** — every `LEGACY_REDIRECTS` entry produces a stub with the right target +
  canonical; the output-path-occupancy guard (C-1) is exercised: a stub IS written at a now-free legacy
  segment, and is NOT written over an occupied root path (a published college hub).
- **Uniqueness assert test** — a synthetic duplicate slug makes the **full** build fail loudly (§8 S-1:
  the assert only fires on a full build).
- **YWCC full-build byte-stability** — a full build then a second full build produces byte-identical
  output (idempotency), and the CS profile set matches the current live set.

## 12. Rollout

1. Land this spec → owner review → senior-eng review (hard gate) → owner sign-off.
2. Build TDD per the plan (writing-plans next).
3. Rebuild YWCC into the new tree; eyeball locally; deploy to the Pages repo (batched per
   `feedback_facultyfolio_batch_rebuild`).
4. Later, per college: eyeball → add its slug to `PUBLISHED_COLLEGES` → scoped build → deploy.

## 13. Goals checklist (shipped vs deferred — per the review-against-plan rule)

Filled at implementation time; every goal must end SHIPPED or LOUDLY DEFERRED:
- [x] G1 three-level site (NJIT → college → dept) + flat profiles — SHIPPED
- [x] G2 explicit `PUBLISHED_COLLEGES` registry + auto dept discovery — SHIPPED
- [x] G3 scoped CLI (`--college`, `--dept`, default all) — SHIPPED
- [x] G4 scoped-write safety (ancestor refresh, no sibling writes, no wipe) — SHIPPED, incl. the
  full-build slug-uniqueness assert (`_assert_slug_uniqueness`, distinct-key-per-slug) that underwrites
  the shared `/p/` + photo namespace safety
- [x] G5 SEO (sitemap + robots + canonical + leaderboard redirects) via `SITE_ORIGIN` — SHIPPED
- [x] G6 YWCC restructured into the new tree, tested, deployable — SHIPPED (byte-stable, count==live 119)
- [ ] Deferred-and-flagged: MTSM/HCAD build (seams only — see §14 landmine), stale-pruning, visual (Spec B), profile rename, cross-college search, framework
- **Built as YWCC-only; nothing new published.** Merge + deploy = owner's gate.

## 14. Open decisions when a *dept-less / school-based* college is later published

Recorded so they aren't silently dropped at add-time (not decisions for this spec):
- MTSM hub copy + confirming the leaderboard-at-`/<college>/` shape reads well.
- HCAD child-org heading label ("Departments" vs "Schools" vs a generic/data-driven word).
- Whether Honors College is ever published (KG has it; likely no home faculty).

> ⚠️ **LANDMINE — dept-less college publish (M-4 from the whole-branch review).** The dept-less-college
> path (D8) is a SEAM, not implemented. `build_college_hub` always renders a card-hub and `build_site`
> iterates only `dept_orgs_of_college`, so publishing a college whose faculty sit directly on the college
> node (e.g. MTSM leadership as `faculty@mtsm`) with the CURRENT code would produce an **empty hub + ZERO
> profiles**, while `db.college_coverage` still advertises a non-zero faculty count → a count-vs-pages
> drift. Before publishing any dept-less or school-based college, implement D8 (render the college's own
> leaderboard at `/<college>/`) — do NOT just add its slug to `PUBLISHED_COLLEGES`. (YWCC is unaffected:
> 0 college-node-direct faculty, verified.)

## 15. Changelog — review changes folded (2026-07-09)

Fable (gating review, owner's sign-off proxy): **SHIP-WITH-CHANGES** → sign-off given once C1 landed.
Senior-eng (parallel design review): **APPROVE-WITH-CHANGES** → approve to plan once the three blocking
items are written in. All folded:

- **C1 / SE#3 (blocking):** added `SITE_ORIGIN` (§6); all sitemap/robots/canonical URLs now absolute (§9).
- **C-1 / SE#1 (blocking, real defect):** redirect clobber-guard redefined from name-membership to
  output-path-occupancy (§5, §8) — the old guard would have suppressed the required leaderboard redirects.
- **S-2 / SE#2 (blocking):** defined college-aggregate coverage as subtree-DISTINCT + named the helper
  `db.college_coverage` (§7) — a dept-sum would double-count ~12 dup-home faculty.
- **C2/C3 (paths + redirect format):** explicit `paths.py` nested signatures + new hub/sitemap/robots
  path fns; pinned the `LEGACY_REDIRECTS` value format (§5, §7).
- **S-1 coupling:** stated that flat-namespace sibling-safety depends on global slug uniqueness, which
  only the full-build assert enforces (§8, §11).
- **SE#4 / §11:** added tests that ancestor *counts* refresh and that a scoped build's sitemap stays full.
- **D9 correction:** noted `db.dept_orgs_of_college` is already type-agnostic + faculty>0-gated, so HCAD
  "schools" already resolve today; only the heading label is add-time work (§4, §14).
- **C5 (nit):** hub template sharing left as a build-time call (§7).

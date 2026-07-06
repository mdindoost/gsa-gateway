# FacultyFolio — YWCC departments + college hub (small generalizability)

**Date:** 2026-07-06
**Status:** Design approved (owner + Fable review). Delta-spec — mirrors the shipped CS pilot.
**Predecessor:** `2026-07-06-facultyfolio-crawled-research-awards-service-design.md`,
`2026-07-05-facultyfolio-display-flags` (Task 7 URL restructure — deferred here).

## Goal

Generate FacultyFolio pages for the **remaining two YWCC departments** (Data Science,
Informatics) plus a **simple YWCC college hub**, by doing the *smallest* generalization that
lifts the generator off its single hardcoded department (Computer Science). Defer the full
multi-college abstraction (MTSM/NCE/CSLA/HCAD) and the flat→hierarchical URL restructure.

**Owner hard requirement (verbatim):** "nothing hardcoded and everything be generalizable for
other department and colleges based on our DB." No per-dept/per-college lookup tables or
vocabulary. `format.py` stays mechanical-only (base spec §3.4).

## Current state (CS pilot, live)

- 57 CS profile pages `p/<slug>.html` + one 3-view leaderboard `cs/index.html`.
- Root `index.html` is a meta-refresh redirect → `cs/index.html`.
- Six CS-bound entry points: `config.CS_ORG_ID=16`; `db.cs_faculty_slugs()`;
  `rank.roster()` (ignores its `org_id` arg — latent bug); `build.build_all`/`build_leaderboard`
  (literal `"Computer Science"`, `CS_ORG_ID`, single leaderboard); `paths.leaderboard_path`
  (hardcodes `cs/`); `render` (`"NJIT-CS"` sources label + `../cs/index.html` back-link).

## KG facts (verified against the live DB, 2026-07-06)

- YWCC college = node **299** (org slug `ywcc`, `organizations.name='YWCC'`).
- Dept children via `edges type='part_of' dst_id=299 is_active=1`, `type='Org'`:
  - Computer Science — node 16, slug `computer-science`, **57** home faculty
  - Data Science — node 73, slug `data-science`, **21**
  - Informatics — node 100, slug `informatics`, **41**
  - College Administration — node 2, slug `college-administration`, **0** faculty (staff only)
- Roster = HOME appointment only: `has_role` + `category='faculty'` + `dst_id=<org node>`.
  **Rosters are disjoint** (verified: no person holds `faculty` edges to >1 of {16,73,100};
  zero `faculty` edges directly on node 299). Joint/affiliated/emeritus/admin/staff excluded by
  the `category='faculty'` filter — a person home *outside* YWCC with a joint YWCC edge gets no page.
- Org node → slug: `nodes.attrs` JSON `org_id` → `organizations.slug`.
- The full name "Ying Wu College of Computing" exists **nowhere in the DB** (org name is the
  acronym `YWCC`). → `config.COLLEGE_NAMES` remains the only source (see §D).

## Design

### A. Parameterize the six CS-bound entry points (org-node-id driven, no new maps)

1. `db.faculty_slugs(org_id)` — replaces `cs_faculty_slugs()`; parametric, keeps `ORDER BY n.name`,
   still filters `config.SUPPRESSED`. CS behavior byte-identical (same SQL, same param).
2. `rank.roster(org_id)` — call `db.faculty_slugs(org_id)` instead of `db.cs_faculty_slugs()`.
   **Fixes the latent bug** (arg was ignored). Zero risk for CS: sole caller passed `CS_ORG_ID`,
   so the generated SQL/param are unchanged.
3. `db.org_meta(node_id)` → `(slug, name)` read from the KG (`nodes.attrs.org_id` →
   `organizations.slug`). **URL segment = the org slug** — mechanically derived, generalizable,
   no `cs`/`ds` short-code map.
4. `paths.leaderboard_path(out_root, segment)` — parameterized segment. Profiles stay
   `p/<slug>.html` (flat shared namespace; NJIT slugs are globally unique per person).
5. `render.render_profile`:
   - sources label → `"Scholar + NJIT"` / `"NJIT"` (drop `-CS`).
   - profile back-link → the person's **home-dept** leaderboard, via a `home_dept_segment`
     threaded through the render ctx (derived from the home-dept org slug), not hardcoded.
6. `build.build_dept(org_node_id, out_root)` — builds one org's profiles + its leaderboard.
   `build.build_all(out_root)` — **discovers YWCC's dept children from the KG**
   (`part_of` node 299, faculty>0, **`ORDER BY organizations.slug`** for determinism), dedups
   profile slugs across depts, builds each dept leaderboard, then the hub, then copies assets.
   One entry point, one deploy. Adding a 4th YWCC dept later = zero code; College Administration
   (0 faculty) auto-excluded (and never gets a leaderboard).

**Cross-dept profile dedup is LOUD** (Fable required change 3): rosters are disjoint today, so
dedup is a no-op — but the deferred multi-home producer bug can revert ~12 people to dup-home on
the next re-crawl. If a slug appears in two dept rosters, **log a warning (slug + both depts) and
keep the first in sorted build order** (deterministic) — never a silent skip that would mask the
data regression.

### B. YWCC college hub page (new, small)

- New template `templates/hub.html` (extends `base.html`): college title + one card per dept
  (name, N home faculty, M with Google Scholar, "View" link → `<segment>/index.html`). Card
  data from the existing `rank.coverage(org_id)` (returns `(N_with_scholar, M_total)` — hub must
  read the tuple in that order; add a test).
- Pure `render.render_hub(college_name, cards)` + `build.build_hub(college_node_id, out_root)`
  writes root `index.html` (replaces the redirect).
- **Leadership section — LOUDLY DEFERRED to a next step** (owner: "for now simple hub but next
  step it will have leadership"). Not built here. Placeholder noted; appears in the goals checklist
  as deferred.

### C. URLs

- Root `index.html` → the YWCC hub (was the redirect).
- Dept leaderboards → `computer-science/`, `data-science/`, `informatics/` (= org slug).
- CS moves `cs/index.html` → `computer-science/index.html`; keep a tiny `cs/index.html`
  meta-refresh stub → `computer-science/index.html` so the one existing live URL never 404s.
  (All `p/*.html` back-links regenerate in the same single end-of-series deploy → no broken-link
  window.)
- Profiles unchanged: `p/<slug>.html`.
- Hierarchical `/ywcc/<dept>/…` restructure stays **deferred** to "full" generalizability (Task 7).

### D. Generalizability guard

- The YWCC anchor lives in `config.py` resolved **by slug** (`COLLEGE_SLUG = 'ywcc'` →
  node id at startup) — slugs survive a `run_explore.py --reset` re-derive, which renumbers node
  ids. No bare `299` in `build.py` (Fable required change 4). This is an entry-point anchor (same
  class as a crawler `EntryPoint`), not per-dept vocabulary.
- `CS_ORG_ID` is retired/renamed once `build_dept` is parametric, so nothing tempts a future
  CS-bound call.
- `config.COLLEGE_NAMES` stays — the only source of the full college name (absent from the DB).
  Acceptable proper-noun map (closed set, identity display), **not** a content-curation
  dictionary. The honest "full" fix (a gated migration adding `full_name` to
  `organizations.metadata` so the DB is the source) is recorded in the deferred list.
- Remaining hardcodes after this change, all justified: `COLLEGE_NAMES` (data absent from DB),
  the single YWCC slug anchor (entry-point class), `RANK_LADDER` (closed ordinal scale,
  mechanical — verified to cover DS/Informatics titles), empty `SUPPRESSED`/`PHOTO_OVERRIDES`.
  No per-dept vocabulary anywhere; every URL segment derives from `organizations.slug`.

## Testing (extend the existing suite)

- `db.faculty_slugs(org_id)`: CS=57, DS=21, Informatics=41; still excludes `SUPPRESSED`.
- `rank.roster(org_id)` **honors its arg** (regression for the latent bug) — DS roster ≠ CS roster.
- `render` sources label generalized (`"Scholar + NJIT"` / `"NJIT"`, no `-CS`); profile back-link
  uses the home-dept segment.
- `render.render_hub`: card fields, coverage counts in the right `(N,M)` order, HTML escaping.
- `build.build_all`: builds 3 dept leaderboards + hub + assets; College Administration excluded
  (0-faculty dept → no leaderboard); dept discovery order deterministic.
- CS profile output **byte-stable except three intended diffs**: moved segment, back-link, and
  the sources label (Fable required change 2).
- Add the new verification questions to `eval/questions.txt` per the grow-correctness-suite rule
  (DS/Informatics roster + hub sanity).

## Build order

Parameterization first (unlocks all) → **DS pages → Informatics pages → hub** → tests →
senior-eng review of the diff → owner sign-off → **rebuild + redeploy ONCE** at the end (owner
eyeballs all together, per the batch-rebuild rule) → memory + commit.

## Goals checklist

- [ ] A. Six entry points parameterized (org-node-id driven); `roster` latent bug fixed.
- [ ] B. YWCC hub page (dept cards + coverage).
- [ ] C. URLs: root=hub, dept segments = org slug, `cs/` redirect stub.
- [ ] D. Generalizability guard: slug anchor, `CS_ORG_ID` retired, `COLLEGE_NAMES` justified+flagged.
- [ ] Tests extended; CS byte-stable except the 3 intended diffs.
- **DEFERRED (loud):** hub Leadership section (next step); full multi-college abstraction;
  hierarchical URL restructure (Task 7); `organizations.metadata.full_name` migration to retire
  `COLLEGE_NAMES`; multi-home producer durability (dedup guards it meanwhile).

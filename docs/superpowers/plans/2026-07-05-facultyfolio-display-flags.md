# FacultyFolio Display-Mode Flags — Plan / Todo

> **Status:** DESIGN + TODO — no coding yet (owner will greenlit each flag step by step).
> **Owner:** Mohammad. **Author session:** 2026-07-05. **Base:** main `9603e7f`.
> **Reuses:** the shipped FacultyFolio generator (`facultyfolio/`, 47 tests). This is a
> delta on top of it — see `docs/superpowers/specs/2026-07-03-facultyfolio-generator-design.md`.

## Goal
Stop hard-coding one behavior per page component. Give each configurable component a
**display-mode flag** so the owner flips how it renders. Today every component is coded the
**Adaptive** way (show a thing only when its data exists). We add a **Fixed** mode
(show the *full* set always; **gray the missing ones**) and keep Adaptive as the other option.
Decide each flag's default one at a time.

## The concept (locked with owner)
- Each component gets a flag with named string options. **Convention:** an UPPERCASE setting
  per component, e.g. `SOCIAL_ICONS = "Fixed"` / `"Adaptive"`.
- **`Adaptive`** = the current code, preserved unchanged (never deleted — it's one option).
- **`Fixed`** = show every possible item on every page; items the person has render active,
  the rest render **grayed / disabled** (present but muted, optionally a "Not listed" hint).
  This is the cooperation nudge: a faculty member sees exactly what's missing from their page.
- Flags live in one place (`facultyfolio/config.py`), read by `render.py` / the templates.
- Defaults are **not** decided up front. Only `SOCIAL_ICONS` default is chosen: **`Fixed`**.
  Every other flag defaults to `Adaptive` until the owner says otherwise, so adding a flag
  is a zero-visual-change commit until its default is flipped.

## Architecture
- **Config:** add a small typed accessor in `config.py` (e.g. `FLAGS` dict or individual
  constants + a `mode(name)` helper) with a validated `{"Fixed","Adaptive"}` value per flag.
- **Render:** `render.py` passes the resolved mode(s) into the template context; templates
  branch `{% if mode == 'Fixed' %}`. Where the choice is data-shaping (e.g. leaderboard
  roster), `render.py`/`rank.py` build the fuller list in Fixed mode.
- **CSS:** each Fixed component needs one **grayed/disabled visual state** in
  `assets/style.css` (muted color, no hover/link affordance, optional "Not listed"). This is
  the only recurring implementation cost; the flag plumbing itself is cheap.
- **Idempotence + tests:** every flag ships with tests for BOTH modes; the site regenerates
  byte-stably per mode. Grow the FacultyFolio test suite per [[feedback_grow_correctness_suite]].

## Candidate components (6 strong fits)

### Flag 1 — `SOCIAL_ICONS` (PILOT · default **Fixed**)
- **File(s):** `profile.html` (`.social` block), `render.py` (profiles ctx), `style.css`, `config.py`.
- **Adaptive:** only the profiles the person has (current: Email/Website/Scholar/GitHub/LinkedIn conditionals).
- **Fixed:** all 6 in a stable order — **Email · Google Scholar · Website · LinkedIn · GitHub · ORCID** —
  present → active, missing → grayed non-link. **Dedup rule:** if `website.url == scholar.url`
  (or equals any other profile's url), render Website grayed (avoid two live icons to the same
  place). Currently affects 1/57 (Houle). Coverage today: Scholar 39, Website 30, LinkedIn 26,
  GitHub 1, ORCID 0, Email ~all.
- **Note:** ORCID has no icon in the template yet — add its SVG as part of this task.

### Flag 2 — `ABOUT_ROWS` (default Adaptive)
- **File(s):** `profile.html` (Background block), `render.py`, `style.css`.
- **Adaptive:** each row (Appointment always; Education / Office / Teaching interests / Teaching)
  omitted when empty.
- **Fixed:** all rows always, missing shown grayed with a "Not listed" value. Highest-value
  after social icons — directly shows what a faculty page is missing.

### Flag 3 — `SCHOLAR_METRICS` (default Adaptive)
- **File(s):** `profile.html` (Impact & trajectory block), `render.py`, `style.css`.
- **Adaptive:** whole metrics block replaced by a claim-hook when no Scholar link.
- **Fixed:** always show the 4-metric skeleton (Citations / h-index / i10 / Active-since) with
  grayed `—` values + the hook. Chart stays gated on ≥4 years (a sub-rule, not its own flag).

### Flag 4 — `PUBLICATIONS` (default Adaptive)
- **File(s):** `profile.html` (Publications section), `render.py`, `style.css`.
- **Adaptive:** entire section vanishes when no Scholar/pubs.
- **Fixed:** section always present with an empty-state ("No publications linked yet — claim…").

### Flag 5 — `NAV` (default Adaptive) — folds in the nav content edit
- **File(s):** `base.html` (top nav), `render.py` (needs per-page "which sections exist"), `style.css`.
- **Content change (independent of mode, do here):** remove **Teaching** from the nav (teaching
  is shown at the top in Background); add jump-links to the **Scholarly-activity** and
  **Recognition** sections. Add `id`s to those sections so the links scroll. Proposed short nav
  labels: **Impact** (→ Impact & trajectory) and **Recognition** (→ Awards & honors) — owner to
  confirm short vs full ("Scholarly activity" / "Awards & honors").
- **Adaptive:** nav lists only links to sections present on that page.
- **Fixed:** fixed link set on every page; links to an absent section render grayed/disabled.

### Flag 6 — `LEADERBOARD_ROSTER` (default Adaptive)
- **File(s):** `leaderboard.html`, `rank.py` (build full roster), `render.py`, `style.css`.
- **Adaptive:** only the 39 with-Scholar faculty; the other 18 excluded (current "39 of 57").
- **Fixed:** list **all 57** — the 18 without Scholar grayed at the bottom (no rank/metrics,
  "no Scholar data" tag). Same "show all, gray the gaps" pattern at roster scale.

## Weak fits — leave Adaptive (reported, not planned)
Title / Joint-appointment / College lines (single facts — graying reads oddly); Photo (already
degrades to a monogram); Areas of focus (already dual-state with a hook); provenance "Synced…"
line (trivial). Revisit only if the owner asks.

## Task ordering (TDD, one flag per task, each independently mergeable)
Each task: write both-mode tests → `config` flag → `render`/template branch → gray CSS →
regenerate + eye-check → diff to owner → merge. Per the hard gate, each non-trivial task gets a
senior-eng review + owner sign-off before merge ([[feedback_senior_eng_review]]).

- [ ] **Task 0 — Flags scaffold:** `config.py` flag accessor + validation; `render.py` passes
  modes into context. No visual change (all default Adaptive except the pilot). Tests: invalid
  value rejected; default map correct.
- [ ] **Task 1 — `SOCIAL_ICONS` (default Fixed):** ORCID icon added; Fixed = all-6 + gray missing
  + website==scholar dedup; Adaptive unchanged. Gray `.social a.off` CSS. Pilot proves the pattern.
- [ ] **Task 2 — `ABOUT_ROWS`.**
- [ ] **Task 3 — `SCHOLAR_METRICS`.**
- [ ] **Task 4 — `PUBLICATIONS`.**
- [ ] **Task 5 — `NAV`** (includes the Teaching-out / Impact+Recognition-in content edit + section ids).
- [ ] **Task 6 — `LEADERBOARD_ROSTER`.**

Order after Task 1 is flexible — owner picks the next each step and decides its default then.

## Open decisions (owner, as we go)
1. Default per flag (only `SOCIAL_ICONS=Fixed` decided; rest default Adaptive until flipped).
2. Nav labels: short (**Impact** / **Recognition**) vs full (Scholarly activity / Awards & honors).
3. "Not listed" wording + exact gray treatment (confirm on the pilot, reuse everywhere).
4. Website==Scholar dedup in Fixed mode — confirm gray-the-duplicate.

## Site structure / hierarchical URLs (DECIDED 2026-07-05 — own spec + task)
Separate effort from the display flags; the org tree (`organizations.parent_id`) already
models the hierarchy, so the URL scheme = the org path. Decisions:
- **Multi-tenant:** `facultyfolio.<domain>/njit/…` — `njit` is a tenant segment (other
  universities later). Owner intends to buy the `facultyfolio` domain, served at root.
- **People pages FLAT:** `/njit/people/<slug>` — one canonical URL per person (clean for joint
  appointments; a person is listed/linked from every dept they belong to but has ONE page).
- **College + dept pages:** each gets its own page — `/njit/ywcc/` (college: its depts +
  college-wide leaderboard) and `/njit/ywcc/computer-science/` (dept: leaderboard + roster).
- **University index:** `/njit/` lists colleges (shows only YWCC now, grows).
- **Slugs:** full org slugs (`computer-science`, `data-science`, `informatics`) — no alias map.
- **Scope now:** all of **YWCC** (CS + Data Science + Informatics), not just CS; all colleges
  eventually. (FacultyFolio currently builds CS only — this task expands it.)
- **Links:** introduce `paths.py` as the single source of truth for output locations + relative
  hrefs (per-page depth aware), so the flat→hierarchical swap is a one-module change. A SEED of
  this (output-path SSOT used by `build.py`) lands in Task 0; the full per-page href refactor +
  page-type generation lands here.

- [ ] **Task 7 — Hierarchical site structure (own spec first, then TDD):** page types
  (university/college/dept/person), YWCC scope expansion, joint-appointment canonical URLs,
  `paths.py` per-page href generation, navigation. Full expert-review gate before build.

## Related
[[project_faculty_page_builder]] (the shipped generator this extends), [[project_office_data_gap]]
(the Office row's data gap — Fixed `ABOUT_ROWS` will surface it as "Not listed" until backfilled),
[[feedback_senior_eng_review]], [[feedback_reuse_prior_designs]], [[feedback_user_owns_decisions]].

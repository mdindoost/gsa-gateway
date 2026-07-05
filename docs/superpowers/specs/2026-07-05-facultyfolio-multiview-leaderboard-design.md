# FacultyFolio Multi-View Leaderboard — Design

> **Status:** DESIGN — for owner review, then plan → expert review → TDD build.
> **Owner:** Mohammad (decided: 3 views; default = By rank/title). 2026-07-05.
> **Supersedes:** Task 6 (`LEADERBOARD_ROSTER` flag) in the display-flags plan — the multi-view
> shows all faculty inherently, so the separate "gray the missing" flag is absorbed here.

## Goal
Replace the single citation-ranked leaderboard (which shows only the 39 faculty with Google
Scholar and hides the other 18) with **one page offering three views**, toggled client-side:
**By rank/title** (default), **By citations**, **A–Z**. Every view shows **all faculty in scope**
(57 for CS), so nobody is hidden (this is the concrete fix for "Zaidenberg isn't on the leaderboard").

## Current state
- `rank.ranked_list(org_id)` → the 39 with Scholar, sorted by citations desc, ranked 1..N.
- `rank.coverage(org_id)` → `(39, 57)`.
- `render.render_leaderboard(org_name, ranked, coverage)` → `leaderboard.html` → `cs/index.html`.
- The Publications section already uses the toggle pattern we'll reuse (pre-rendered lists, a JS
  show/hide switch) — `profile.html` `.toggle` buttons + the `{% block script %}` handler.

## The three views

### 1. By rank/title — DEFAULT
All faculty grouped by academic rank, groups in seniority order, a section header per group.
Within a group, secondary sort = **citations desc, then A–Z** (so within "Professor", the most-cited
first; no-Scholar professors A–Z after). Rows show name + title + (citations/h-index if present, else
"—"). This reads like a department directory, not a scoreboard, and every one of the 57 appears.

### 2. By citations
Flat ranking by citations desc. The faculty with Scholar are ranked **1..39**; the 18 without Scholar
are listed **after**, grayed, with **no rank number** and "—" for the metrics (same "show all, gray the
gaps" pattern as the icon/row flags). This preserves today's ranking while still surfacing everyone.

### 3. A–Z
All faculty sorted alphabetically by **surname** (the KG stores "Last, First" for some — normalize via
the existing `format.normalize_name`, sort on the surname token). Metrics shown if present, else "—".

## The rank ladder (grounded in the real CS titles)
Titles actually stored on CS faculty edges (this session): Distinguished Professor (3, incl. compound),
Professor (10 + "Professor, Department Chair" / "Professor, Associate Dean…"), Associate Professor (8),
Assistant Professor (9), Senior University Lecturer (15), University Lecturer (7); plus edge cases
"Dean, Ying Wu College of Computing", "…Director", and one empty.

**Ladder (seniority order):**
```
1 Distinguished Professor
2 Professor
3 Associate Professor
4 Assistant Professor
5 Senior University Lecturer
6 University Lecturer
7 Other            (leadership-only titles like "Dean, …", "…Director", or empty — bucket last)
```

**Matching rule (mechanical, longest-phrase-first):** to avoid "Professor" matching "Associate
Professor", match a person's title string against the known rank **phrases ordered by specificity**
(longest first): `Distinguished Professor` · `Associate Professor` · `Assistant Professor` ·
`Senior University Lecturer` · `University Lecturer` · `Professor` · `Lecturer`. The first phrase that
appears (case-insensitive, word-boundary) sets the rank; its seniority index comes from the ladder.
Compound titles resolve correctly ("Professor, Department Chair" → Professor; "Distinguished Professor,
Associate Dean…" → Distinguished Professor). No professorial/lecturer phrase present → **Other**.

This is a small **closed academic-rank ordering** — the same category of allowed identity/ordering
config as `COLLEGE_NAMES` (proper nouns) and the affiliated fix's `_ROLE_RANK`, NOT a content-curation
dictionary. It lives in `config.py` (`RANK_LADDER`) and is documented as such. Unknown titles never
crash — they fall to "Other".

## Data model
- New `rank.roster(org_id)` → **all** in-scope faculty (the 57), each: `{slug, name, title,
  rank_index, rank_label, citations|None, h_index|None}`. Built from `db.cs_faculty_slugs()` +
  `db.get_faculty` (reuses existing reads; Scholar fields None when absent).
- New pure helpers (in `rank.py`, testable, no HTML):
  - `by_rank(roster)` → `[{label, members:[…]}]` groups in ladder order, members secondary-sorted.
  - `by_citations(roster)` → `[…ranked 1..N…] + [.. unranked, grayed ..]`.
  - `by_name(roster)` → surname-sorted list.
- `rank.coverage(org_id)` stays for the "N of M with Scholar" line on the citations view.

## Rendering
Follow the existing toggle pattern: **pre-render all three views server-side** into `leaderboard.html`
(three containers), a small JS switch shows the active one and toggles the button `.on` state. Keeping
the sort/group logic in Python (pure, unit-tested) and JS trivial (show/hide) mirrors Task 1/2 and the
pub toggle. Default-visible container = **By rank/title** (a `config.LEADERBOARD_DEFAULT_VIEW` constant,
so the default is one-line configurable).

- A view switcher (three buttons: Rank · Citations · A–Z) at the top of the leaderboard.
- Rank view: group headers (rank_label) + rows. Citations view: numbered rows + grayed tail. A–Z: flat.
- Grayed no-Scholar rows reuse the established `.off`/muted treatment.
- Coverage/eyebrow wording per view: citations keeps "Ranked among 39 of 57 with Google Scholar";
  rank/A–Z show a neutral "57 faculty" line.

## Testing
- `by_rank`/`by_citations`/`by_name` pure-function tests: ladder order; compound-title resolution
  (Wang "Distinguished Professor, Associate Dean" → Distinguished; "Professor, Department Chair" →
  Professor); Associate/Assistant not mismatched to "Professor"; unknown/empty → Other; no-Scholar rows
  land unranked/grayed and still appear; surname sort on a "Last, First" name.
- Render test: all three containers present; default = rank view visible; a no-Scholar person (acz6
  Zaidenberg) appears in every view; toggle buttons present.
- Idempotent rebuild stays byte-stable. Grow `eval`/tests per [[feedback_grow_correctness_suite]].

## Goals checklist (to verify at build end)
- [ ] Three views: By rank/title (default), By citations, A–Z
- [ ] Every view shows all 57 (Zaidenberg reachable in each)
- [ ] Rank ladder + longest-phrase-first matching + Other bucket, in config
- [ ] Client-side toggle (pre-rendered, JS show/hide), default configurable
- [ ] Citations view preserves 1..39 ranking + grayed no-Scholar tail
- [ ] Supersedes Task 6 flag (noted)

## Open decisions (owner)
1. **"Other" bucket** — is bucketing pure-leadership titles (Dean-only, Director) at the bottom OK, or
   should Dean/Chair sit at the top as "Leadership"? (Default: Other-at-bottom; refine later.)
2. **Secondary sort inside a rank group** — citations desc then A–Z (proposed), or pure A–Z?
3. **Metrics in the rank/A–Z views** — show citations/h-index columns (else "—"), or name+title only?

## Related
[[project_faculty_page_builder]] (the generator), the display-flags plan (Task 6 superseded),
[[feedback_senior_eng_review]], [[feedback_no_caps_specialist]], [[feedback_gsa_equal_not_privileged]]
(a directory view is non-competitive — fits the equal-treatment spirit).

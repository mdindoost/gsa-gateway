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
All faculty grouped by academic rank, groups in ladder order (Chair first), a section header per group.
Rows show **name + title only — NO citation/h-index columns** (owner: metrics belong to the citations
view). Within a group, sort **A–Z by surname** (metrics aren't shown, so an A–Z order reads cleanly).
This is a department directory, not a scoreboard; every one of the 57 appears.

### 2. By citations
Flat ranking by citations desc — the ONLY view with metrics. Faculty with Scholar are ranked **1..39**;
the 18 without Scholar are listed **after**, grayed, with **no rank number** and "—" for the metrics
(the "show all, gray the gaps" pattern). Absence is fine — a no-Scholar person simply shows no numbers.

### 3. A–Z
All faculty sorted alphabetically by **surname**, **name + title only, no metrics**. (Names are already
`normalize_name`-flipped to "First Last" upstream, so the surname is the last token.)

## The rank ladder (grounded in the real CS titles)
Titles actually stored on CS faculty edges (this session): Distinguished Professor (3, incl. compound),
Professor (10 + "Professor, Department Chair" / "Professor, Associate Dean…"), Associate Professor (8),
Assistant Professor (9), Senior University Lecturer (15), University Lecturer (7); plus edge cases
"Dean, Ying Wu College of Computing", "…Director", and one empty.

**Ladder (leadership first, then seniority) — owner: department Chair heads the directory:**
```
0 Department Chair   (leads the unit — FIRST, regardless of professorial rank)
1 Distinguished Professor
2 Professor
3 Associate Professor
4 Assistant Professor
5 Senior University Lecturer
6 University Lecturer
7 Other              (e.g. a Director, or empty — bucket last;  Dean placement = open decision #1)
```

**Matching rule (mechanical, two-pass):**
- **Pass 1 — leadership marker:** if the title contains the phrase **"Department Chair"** (match this
  full phrase, NOT bare "Chair" — a bare/"Chair Stipend"/"Endowed Chair" is a *professorship honor*,
  not unit leadership), the person is **Department Chair (rank 0)**, overriding their professorial rank.
  So "Professor, Department Chair" → rank 0 (top), not Professor.
- **Pass 2 — professorial rank, longest-phrase-first:** otherwise match against the rank **phrases
  ordered by specificity** (longest first): `Distinguished Professor` · `Associate Professor` ·
  `Assistant Professor` · `Senior University Lecturer` · `University Lecturer` · `Professor`. First
  phrase that appears (case-insensitive, word-boundary) sets the rank. This keeps "Associate
  Professor" from mis-matching bare "Professor" and resolves compounds ("Distinguished Professor,
  Associate Dean…" → Distinguished Professor; "Professor, Department Chair" is already Pass-1 Chair).
- **Pass 3 — Dean → Professor (owner):** if still unmatched AND the title contains "Dean" (the bare
  "Dean, Ying Wu College of Computing" case — a dean holds a full professorship), rank as **Professor**.
  (Compound "…, Associate Dean" titles never reach here — their professorial rank matched in Pass 2.)
- **Else → "Faculty"** — a neutral catch-all group (NOT "Other") for the 2 rank-less titles (a Director,
  an empty title), placed after the lecturer groups. There is no "Dean at the bottom".

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

## Page add-ons (owner picked all four)
All four are cheap, client-side or build-time, and reuse existing data:
- **Live search box** — one text input filters the visible rows across all views by name / title /
  research-area text (client-side, case-insensitive substring on data-attributes). Empty query = all.
- **At-a-glance strip** — a summary line built at render time from the roster: total faculty, the
  per-rank counts (from `by_rank` group sizes), and "N on Google Scholar" (from `coverage`). Pure Python.
- **Photo thumbnails in rows** — each row shows the person's cached photo (monogram fallback). Requires
  the leaderboard build to resolve a `photo_ref` per person (call `photos.ensure_photo` per slug, same
  as `build_one`; cached → no new fetches). Same monogram SVG treatment as the profile card.
- **Research-area tags in rows** — up to a few area chips per person (from `db.get_faculty(...)["areas"]`,
  already deduped/ordered). Shown in the rank & A–Z rows (40/57 have areas; the rest show none). This
  extends the "name + title" rows with descriptive tags (NOT metrics — the no-metrics rule stands).

**Row content, final:**
- **Rank & A–Z views:** photo · name · title · area tags. No citation/h-index.
- **Citations view:** photo · name · title · citations · h-index (grayed tail = no numbers).

Search + the switcher are the only JS; both trivial (filter rows, toggle panels). Keep sort/group/stats
in Python. The roster carries `data-name/​data-title/​data-areas` on each row so search is a pure DOM filter.

## Implementation notes (folded from the senior design review — must-fix)
1. **Within-group sort = A–Z** (rank view shows no metrics): `key=(surname, name.casefold(), slug)`.
   The None-safe citations key `(citations is None, -(citations or 0), name, slug)` is used ONLY in
   the By-citations view (where `None` must sort last without comparing to `int`).
2. **`by_citations` grayed tail is ordered** — the 18 without Scholar sort A–Z by name then slug
   (deterministic, byte-stable), after the ranked 1..N.
3. **Surname sort** — names are already `normalize_name`-flipped to "First Last" before `by_name`,
   so the surname is the **last** token: `key=(name.split()[-1].casefold(), name.casefold(), slug)`.
   The `slug` tail resolves the real duplicate surnames (Li×2, Wang×2).
4. **`slug` is the ultimate tie-break on EVERY sort** → idempotent, byte-stable rebuilds.
5. **Chair marker (Pass 1)** gets the same care: match the full phrase "Department Chair" (not bare
   "Chair"); a Chair with no other rank still sorts within group 0.

## Implementation notes (should-do, folded)
6. **One ladder, derive the match order** — store a single ordered `RANK_LADDER` of canonical labels
   in config; derive the substring-safe professorial match order in `rank.py` by sorting labels
   **longest-first** (if A contains B then len(A)>len(B), so A is searched first — provably safe, no
   parallel hand-maintained list). Drop the defensive bare `"Lecturer"` phrase → unmatched = Other
   (honest, no guessing "Teaching Lecturer" into a rank).
7. **Build `roster` via `get_faculty` per slug** (not by extending `_members`) so each leaderboard
   title is byte-identical to that person's profile-page title — zero duplicated title-join logic.
8. **a11y** — add `aria-pressed` on the switcher buttons + tab semantics on the panels (cheap win
   over the existing pub-toggle baseline).

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
- [ ] Rank ladder in config; Chair→group 0, Dean→Professor, longest-phrase-first, "Faculty" catch-all
- [ ] Rank & A–Z rows: photo · name · title · area tags (NO metrics); citations view has the metrics
- [ ] Live search box (name/title/area, client-side)
- [ ] At-a-glance strip (totals · per-rank counts · N on Scholar)
- [ ] Photo thumbnails (cached, monogram fallback) + research-area tags in rows
- [ ] Client-side view toggle (pre-rendered, JS show/hide), default configurable
- [ ] Citations view preserves 1..39 ranking + grayed no-Scholar tail
- [ ] Byte-stable idempotent rebuild (slug tie-break everywhere)
- [ ] Supersedes Task 6 flag (noted)

## Decisions (owner)
- ✅ Department **Chair heads the directory** (group 0, above Distinguished Professor).
- ✅ **Rank & A–Z views: name + title only, no metrics**; metrics only in the citations view.
- ✅ **Within a rank group: A–Z** by surname (follows from no-metrics).
- ✅ **Dean = Professor** (owner): the Dean is faculty, not a bottom bucket — Pass 3 maps a bare "Dean"
   title to the Professor group; the Dean sorts A–Z there and appears normally in A–Z/citations views.
- ✅ **Add-ons: all four** — live search box, at-a-glance strip, photo thumbnails, research-area tags.
- No "Other" bucket; a neutral "Faculty" catch-all holds only the 2 truly rank-less titles (Director, empty).

## Related
[[project_faculty_page_builder]] (the generator), the display-flags plan (Task 6 superseded),
[[feedback_senior_eng_review]], [[feedback_no_caps_specialist]], [[feedback_gsa_equal_not_privileged]]
(a directory view is non-competitive — fits the equal-treatment spirit).

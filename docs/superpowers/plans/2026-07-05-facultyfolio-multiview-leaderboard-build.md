# Multi-View Leaderboard + Photo Controls — Build Plan

> **For agentic workers:** TDD, one task at a time; each ends with a green test + a commit.
> **Spec:** `docs/superpowers/specs/2026-07-05-facultyfolio-multiview-leaderboard-design.md`.
> **Goal:** Replace the single citation-ranked leaderboard with a 3-view directory (rank/citations/A–Z,
> all 57 shown) + search + at-a-glance strip + photo/area rows; and add NJIT-first photos + per-person
> override. **Branch:** `feat/facultyfolio-multiview-leaderboard`.

## Global constraints
- Read-only DB (`mode=ro`); no LLM prose; autoescape on. Sort/group/stats in **pure Python** (`rank.py`);
  JS only for view-toggle + search (trivial DOM). Every sort ends in a **`slug`** tie-break (byte-stable).
- Photos are assets, never DB. Override wins over auto order. `PHOTO_OVERRIDES` starts empty.
- Each task: write failing test → implement → `pytest` green → commit. Senior-eng review before merge.

---

### Task 1 — Photos: NJIT-first + per-person override (c)
**Files:** `facultyfolio/photos.py`, `facultyfolio/config.py` (+ `PHOTO_OVERRIDES={}`), `tests/test_photos.py`.
- Reorder auto resolution to **override → cached → NJIT → Scholar → monogram**.
- `_override(slug, out_dir)`: (1) a file `assets/photos_manual/<slug>.*` (copy to output, return ref);
  (2) else `PHOTO_OVERRIDES.get(slug)` → `"njit"`/`"scholar"` force that source, a URL/path → fetch/copy.
  Drop-in file beats config.
- Tests: override file wins over a cached photo; `PHOTO_OVERRIDES[slug]="scholar"` forces Scholar even
  when NJIT has a photo; `="njit"` forces NJIT; URL directive fetches it; no override → NJIT-before-Scholar;
  monogram when all empty; cache still short-circuits when no override.

### Task 2 — RANK_LADDER + `rank_of(title)` matcher
**Files:** `facultyfolio/config.py` (`RANK_LADDER` ordered labels), `facultyfolio/rank.py` (`rank_of`), `tests/test_rank.py`.
- `RANK_LADDER` = ["Department Chair","Distinguished Professor","Professor","Associate Professor",
  "Assistant Professor","Senior University Lecturer","University Lecturer"]; derive the substring-safe
  professorial match order in code by sorting the professorial labels **longest-first**.
- `rank_of(title) -> (index, label)`: Pass 1 "Department Chair"→(0,"Department Chair"); Pass 2 professorial
  longest-first; Pass 3 contains "Dean"→Professor; else Faculty catch-all (index just past the ladder,
  label "Faculty").
- Tests (real titles): "Professor, Department Chair"→Chair; "Distinguished Professor, Associate Dean…"→
  Distinguished; "Associate Professor"/"Assistant Professor" not →Professor; "Dean, Ying Wu…"→Professor;
  "…Director"/""→Faculty; "Senior University Lecturer" before "University Lecturer".

### Task 3 — `rank.roster(org_id)`
**Files:** `facultyfolio/rank.py`, `tests/test_rank.py`.
- `roster(org_id)` → list of all in-scope faculty via `db.cs_faculty_slugs()` + `db.get_faculty(slug)`:
  `{slug, name, title, rank_index, rank_label, citations|None, h_index|None, areas}`. (Title via
  get_faculty so it matches the profile page exactly.)
- Tests: len==57; every dict has the keys; Zaidenberg (acz6) present with `citations is None`; a Scholar
  person has int citations; rank_index/label come from `rank_of`.

### Task 4 — `by_rank` / `by_citations` / `by_name`
**Files:** `facultyfolio/rank.py`, `tests/test_rank.py`.
- `by_rank(roster)` → `[{index,label,members}]` groups in ladder order (empty groups dropped); members
  sorted `key=(name.split()[-1].casefold(), name.casefold(), slug)`.
- `by_citations(roster)` → ranked-with-Scholar first (`key=(citations is None, -(citations or 0), name, slug)`),
  each given a 1..N `rank_num`; the no-Scholar tail A–Z (name, slug), `rank_num=None`.
- `by_name(roster)` → all sorted `(surname, name, slug)`.
- Tests: Chair group first; Dean in Professor group; no-Scholar members sort last in group & unranked in
  citations; None never crashes; Li×2/Wang×2 tie-break by slug; every list deterministic.

### Task 5 — At-a-glance stats
**Files:** `facultyfolio/rank.py` (`leaderboard_stats`), `tests/test_rank.py`.
- `leaderboard_stats(roster, coverage)` → `{total, with_scholar, groups:[(label,count)…]}` (per-rank counts
  from `by_rank`). Pure.
- Tests: total==57; with_scholar==39; group counts sum to 57.

### Task 6 — Render: 3 views + switcher + search + strip + rows
**Files:** `facultyfolio/render.py` (`render_leaderboard` new signature), `templates/leaderboard.html`,
`assets/style.css`, `tests/test_render.py`.
- `render_leaderboard(org_name, roster_views, stats, coverage, photo_map)` where `roster_views` = the three
  ordered structures and `photo_map` = `{slug: photo_ref}`. Renders three panels (rank default-visible),
  a 3-button switcher, a search input, the at-a-glance strip. Rows: photo (img or monogram SVG) · name
  (link) · title · area chips (rank/A–Z) OR citations+h (citations view). Each row carries
  `data-name/data-title/data-areas` for search.
- `{% block script %}`: switcher (show/hide panels, `aria-pressed`) + search (case-insensitive substring
  filter over the data-attrs; hides non-matching rows; empty=all).
- CSS: switcher/tabs, search box, stat strip, row thumbnails, area chips, grayed no-Scholar rows.
- Tests: all three panel containers present; default = rank visible; acz6 appears in every panel; Chair
  group header first; search input + 3 buttons present; a no-Scholar row grayed with "—".

### Task 7 — build.py wiring
**Files:** `facultyfolio/build.py`, `tests/test_build.py`.
- `build_leaderboard`: compute `roster`, the 3 views, stats, coverage; resolve `photo_map` by calling
  `photos.ensure_photo` per slug (cached → no refetch); call the new `render_leaderboard`.
- Tests: leaderboard writes; idempotent byte-identical rebuild (existing idempotence test still passes).

### Task 8 — Regenerate, verify, redeploy
- Clear the auto photo cache (`assets/photos/*.jpg`, keep `photos_manual/`) so NJIT-first re-fetches;
  `build_all`; eyeball rank/citations/A–Z + search + a monogram + Zaidenberg reachable.
- Senior-eng review of the full diff → owner sign-off → merge to main → redeploy to `facultyfolio.github.io`.

## Self-review checks
- Every goal in the spec's checklist maps to a task above (3 views, all-57, ladder, rows, search, strip,
  photos+tags, toggle, citations 1..N + tail, byte-stable, NJIT-first, override (c)). None dropped.
- No new DB writes; no LLM prose; slug tie-break on every sort.

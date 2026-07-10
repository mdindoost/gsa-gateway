# FacultyFolio — College Hub: Leadership + College-Wide Rank Rollup

**Date:** 2026-07-09
**Status:** Design — awaiting senior-eng review + owner sign-off (per CLAUDE.md hard gate)
**Scope:** FacultyFolio static site — the college hub page only (`/<college>/index.html`, live: `/ywcc/`)
**Author:** Mohammad Dindoost (design driven with Claude)

## 1. Motivation

The college hub (`https://facultyfolio.github.io/ywcc/`) is currently just a set of doorways:
a card per department (name + faculty/Scholar counts). It carries **no college-level
information of its own** — no leadership, no college-wide numbers. A visitor landing on the
Ying Wu College page learns nothing about who runs the college or its overall shape until
they click into a department.

Every piece of data needed to fix this is **already in the KG** — dean, associate deans, and
department chairs are all `has_role` edges with titles; the per-rank counts are already
computed for each department's leaderboard. This is a **rendering + aggregation** change with
**no new crawl and no new data**.

## 2. Goal

Make the college hub a real landing page by mirroring the *department page's* information
order (title → numbers/stats → chair) up at the college level:

**Page order, top to bottom:**
1. **Title** — college name (unchanged).
2. **Stats** — college-wide coverage line + a rank rollup: the per-department rank-group
   counts (already shown on each dept page) **summed across all departments**, same taxonomy.
3. **Dean** — the college dean, as a person card.
4. **Associate Deans** — as person cards.
5. **Department Chairs** — every department's chair, as person cards, together in one section.
6. **Departments** — the existing department entry-point cards (name + counts), unchanged.

All leadership people (dean, associate deans, chairs) render as the **same person-card
component** the department leaderboard already uses, each linking to their FacultyFolio
profile. The department cards at the bottom stay clean entry points — the chair is **not**
embedded inside them.

### Real data (computed 2026-07-09 with the existing `rank` code, YWCC)

College rollup — `119 faculty · 76 on Google Scholar`:

| Count | Rank |
|------:|------|
| 3 | Department Chair |
| 6 | Distinguished Professor |
| 13 | Professor |
| 16 | Associate Professor |
| 27 | Assistant Professor |
| 31 | Senior University Lecturer |
| 21 | University Lecturer |
| 2 | Faculty |

(Sums verified: CS 57 + Data Science 21 + Informatics 41 = 119; 39 + 15 + 22 = 76.)

Leadership (from `admin@ywcc` roles):
- **Dean:** Jamie Payton — "Dean, Ying Wu College of Computing"
- **Associate Dean, Academic Affairs:** Brook Wu
- **Associate Dean, Research & External Relations:** Guiling Wang

Chairs (the `rank_index == 0` / "Department Chair" title per dept):
- CS → Vincent Oria · Data Science → James Geller · Informatics → Michael Halper

## 3. Non-goals (YAGNI)

- No per-department rank chips on the hub — that detail **already lives** on each dept page's
  "By rank / title" view; duplicating it on the hub is redundant.
- No dean's-message prose, awards, or bios (not in the KG; separate "send-it-to-me" path).
- No change to the NJIT hub, the department pages, or profile pages.
- No new crawl, no schema change.

## 4. Design

### 4.1 Data layer (`db.py` + `rank.py`)

Two small, pure additions — no writes, read-only:

**`db.college_leadership(college_node) -> {"dean": [...], "assoc_deans": [...]}`**
Reads `admin`-category `has_role` edges on the college Org node (the same edges the earlier
query surfaced). For each person returns a minimal dict `{slug, name, title}` where `title`
is the leadership title from `attrs.titles` (the "Dean …" / "Associate Dean …" string).
Classification is title-substring based, matching `rank_of`'s vocabulary:
- title contains "associate dean" → `assoc_deans`
- else title contains "dean" → `dean`
Deterministic ordering: dean(s) first; associate deans sorted by surname. If a college has no
dean edge, the section is simply omitted (empty-safe — never renders an empty "Dean" header).

**`rank.college_rollup(college_node) -> {"total", "with_scholar", "groups"}`**
Aggregates the existing per-department stats. For each dept in
`db.dept_orgs_of_college(college_node)`: build `rank.roster(dept)`, call the existing
`rank.leaderboard_stats(roster, rank.coverage(dept))`, then:
- sum `total` and `with_scholar`,
- merge `groups` (a list of `(label, count)`) by label, **preserving ladder order**
  (`config.RANK_LADDER` then the `"Faculty"` catch-all — the identical order `by_rank` emits).

Because a person's home-faculty appointment is capped at one department (the multi-home fix),
summing per-department home-faculty counts introduces **no double-counting**. This reuses
`rank_of` / `by_rank` verbatim, so the college labels are guaranteed identical to the
department pages'.

The **chairs list** is derived from the same rosters: the `rank_index == 0` member of each
dept's `by_rank` (the "Department Chair" group). Returned as `{slug, name, title, dept_name}`
so the hub can label each chair with their department if desired.

### 4.2 Render layer (`render.py` + `templates/hub.html`)

**Reuse, don't reinvent.** The leadership/chair person cards use the **existing**
`row_dir` + `photo_thumb` macros already defined in `leaderboard.html` (photo/monogram +
name + title + optional area chips, linked to `p/<slug>.html`). To share them, move those two
macros into a small `templates/_person_card.html` and `{% import %}` it from both
`leaderboard.html` and `hub.html` (pure refactor — byte-identical output on the dept pages,
locked by the existing leaderboard golden tests).

Each leadership/chair person is turned into a template row via the existing
`render._lb_row(roster_dict, photo_map)` helper, so photos, monograms, and links resolve
exactly as on the department pages.

`render_hub(...)` gains optional keyword args (all default `None`/empty, so the NJIT hub —
which passes none — is unaffected):
- `stats` — the `college_rollup` dict (coverage line + ordered `(label, count)` groups).
- `leadership` — `{"dean": [rows], "assoc_deans": [rows], "chairs": [rows]}` (template rows).

`hub.html` renders, between the title and the existing `hub-cards` block, in order:
1. a **stats** block rendered with the **exact same markup the department page uses** — the
   `glance` block (`leaderboard.html:79-83`): two `.glance-hd` spans (`{total}` faculty,
   `{with_scholar}` on Google Scholar) then one `.glance-g` span per rank group rendered
   `{count} · {label}`. This consumes the identical `stats` dict shape
   (`{total, with_scholar, groups}`) that `rank.college_rollup` produces, so the college
   rollup looks 1:1 identical to a department's rank stats. The `glance` block is extracted
   to a shared partial (`templates/_glance.html`, `{% import %}`ed by both templates) so there
   is one source of truth and no drift; the department output stays byte-identical (golden test).
2. a **Dean** section (label + card[s]) — omitted if empty;
3. an **Associate Deans** section — omitted if empty;
4. a **Department Chairs** section — each card labeled with its **department**
   ("Computer Science — Vincent Oria"); omitted if empty;
then the existing department cards. All new blocks are guarded by `{% if %}` so a hub without
leadership/stats (e.g. the NJIT hub) renders exactly as today.

### 4.3 Build layer (`build.py`)

`build_college_hub(college_node, college_seg, out_root, photo_map=None)` gains the shared
`photo_map` (already built across all departments before the hub is built — see
`build_site`, which builds every dept scope, then the hub). It:
- calls `rank.college_rollup(college_node)` and `db.college_leadership(college_node)`,
- resolves the chairs from the rollup,
- turns each leadership/chair person into a row via `_lb_row`, resolving any photo **not**
  already in `photo_map` on demand with the same `_resolve_photo(slug, get_faculty(slug), …)`
  fallback `build_dept` uses (covers scoped rebuilds where `photo_map` is partial),
- passes `stats` + `leadership` into `render_hub`.

`build_site` is updated to pass `photo_map` into `build_college_hub` (one-line change).

## 5. Data-honesty considerations

- **Counts are computed, never authored** — the rollup is a live sum of the same code the dept
  pages use; it cannot drift from them.
- **Empty-safe** — any leadership section with no people is omitted, never rendered as an empty
  header (consistent with the ★ Rising "empty board reads as a negative verdict" rule).
- **Title verbatim** — leadership titles come straight from `attrs.titles`; no rewriting.
- **Same person once** — home-faculty cap of 1 guarantees the rollup total equals the sum of
  the department totals with no de-dup needed.

## 6. Testing

New unit tests (pytest, alongside the existing FacultyFolio suite):
- `rank.college_rollup`: sums totals/coverage; merges group labels; preserves ladder order;
  a single-dept college equals that dept's `leaderboard_stats`; verified numbers for YWCC.
- `db.college_leadership`: classifies dean vs associate dean by title; empty when no admin
  edges; deterministic order.
- Chair derivation: one chair per dept from the `rank_index == 0` group; deptless-college safe.
- `render_hub`: with `stats`+`leadership`, the page contains the `glance` block (coverage line
  + one `.glance-g` `{count} · {label}` span per rank group in ladder order), a Dean/Associate
  Deans/Chairs section each with a linked person card (chairs labeled by department); **without**
  them (NJIT hub call) output is byte-identical to today (golden test).
- Macro-extraction refactor: department leaderboard output byte-identical (existing goldens).

Manual: full build, eyeball `/ywcc/`, confirm links resolve and photos render.

## 7. Goals checklist (to be filled at PR time — shipped / deferred)

- [ ] Stats block (coverage line + college-wide rank rollup chips)
- [ ] Dean section (person card)
- [ ] Associate Deans section (person cards)
- [ ] Department Chairs section (person cards)
- [ ] Department entry cards unchanged, below leadership
- [ ] Person-card macro shared between leaderboard + hub (no output drift)
- [ ] NJIT hub unaffected (byte-identical)

## 8. Resolved decisions (owner, 2026-07-09)

1. **Chair label** — each chair card in the "Department Chairs" section is labeled with its
   **department** ("Computer Science — Vincent Oria"). The chair row carries `dept_name`.
2. **Rollup presentation** — render the college rollup with the **exact same `glance` markup
   the department pages use** (`{count} · {label}` per rank group). No separate design; "look
   at the department, do the same." Extracted to a shared partial so there is one source.

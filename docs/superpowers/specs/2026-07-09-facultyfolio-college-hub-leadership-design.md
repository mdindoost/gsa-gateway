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
3. **Departments** — the existing department entry-point cards (name + counts). Placed
   **directly under the stats, above leadership**, so the page instantly reads as a hub (owner,
   2026-07-09) — a student landing here immediately sees the way into each department.
4. **Dean** — the college dean, as a person card.
5. **Associate Deans** — as person cards.
6. **Department Chairs** — every department's chair, as person cards, together in one section.

All leadership people (dean, associate deans, chairs) render as the **same person-card
component** the department leaderboard already uses — photo, name, title, **and their
research-area chips** (the identical card a visitor sees for that person on their department
page; capped at the same `_LB_AREA_CHIPS = 4`, and honestly empty when the KG lists no areas,
e.g. Michael Halper today). Each card links to the person's FacultyFolio profile. The
department entry cards stay clean entry points — the chair is **not** embedded inside them.

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

Leadership (from `admin@ywcc` `has_role` edges; the DISPLAYED title is the ROLE title — the
`titles[]` entry containing "Dean" — NOT the professorial rank, see §4.1):
- **Dean:** Jamie Payton — titles `["Dean, Ying Wu College of Computing"]` → renders "Dean, Ying Wu College of Computing"
- **Associate Dean:** Brook Wu — titles `["Associate Professor", "Associate Dean for Academic Affairs"]` → renders "Associate Dean for Academic Affairs"
- **Associate Dean:** David Bader — titles `["Distinguished Professor", "Associate Dean"]` → renders "Associate Dean"

> **Data note (2026-07-09):** the KG's Associate Deans were stale (had Guiling Wang, missing
> David Bader) vs. the live `computing.njit.edu/administration` page. Corrected live via
> `scripts/_ywcc_assoc_dean_fix.py --commit` (gated, hardened_backup): added Bader's
> `admin@YWCC "Associate Dean"`, removed Wang's `admin@YWCC` role (she keeps her faculty
> appointments). Bader's own profile does NOT state an Associate-Dean title, so a generic
> "Associate Dean" is used (faithful to the admin-page heading, no invented portfolio). The
> crawler (currently paused) could re-derive Wang's edge from her still-stale profile on a
> future run — known multi-source drift, out of scope here.

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

Three small, pure additions — no writes, read-only.

**`db.college_leadership(college_node) -> {"dean": [...], "assoc_deans": [...]}`**
Reads `admin`-category `has_role` edges on the college Org node. For each person returns
`{slug, name, title}` where:
- **`name` is run through `format.normalize_name`** (BLOCKER, senior-eng): raw node names are
  "Surname, Given" (e.g. `"Wu, Brook"`); every other consumer normalizes to "Given Surname",
  and the surname sort below (`rank._surname`, which takes `name.split()[-1]`) is only correct
  on a normalized name. Without this the hub renders "Wu, Brook" and sorts by given name.
- **`title` is the ROLE title, not the rank** (BLOCKER, senior-eng): `attrs.titles` is a
  multi-item list mixing professorial rank and role, e.g. Bader's
  `["Distinguished Professor", "Associate Dean"]`. The displayed title is the **entry
  containing "dean"** (case-insensitive); if none contains "dean" (shouldn't happen for an
  admin-classified leader) fall back to the last entry. So Payton → "Dean, Ying Wu College of
  Computing", Wu → "Associate Dean for Academic Affairs", Bader → "Associate Dean". This is
  verbatim from the KG (§5) — we SELECT which listed title to show, we never reword one.

Classification uses that same role title:
- role title contains "associate dean" → `assoc_deans`
- else contains "dean" → `dean`
Ordering: dean(s) first (multiple deans, if ever, sorted by normalized surname); associate
deans sorted by normalized surname. A section with no people is omitted (empty-safe — never an
empty "Dean" header). A person classified as BOTH (title has "dean" but also a chair role
elsewhere) still appears once here under their dean title; the chairs section is derived
independently (below) and the same person legitimately appearing in two DIFFERENT sections —
e.g. dean + faculty — is acceptable (they hold both roles).

**`rank.college_rollup(college_node) -> {"total", "with_scholar", "groups"}`**
(Revised per senior-eng — simpler and provably order-correct.) Instead of merging per-dept
count tuples, **concatenate the full rosters and rank once**:
1. Collect the org set exactly as `db.college_coverage` does — `dept_orgs_of_college(college_node)`
   **plus the college node itself** (SHOULD-FIX: catches faculty homed directly on the college
   org, e.g. a deptless college like MTSM; YWCC has none today but this mirrors the established
   `college_coverage` pattern and removes a latent landmine).
2. `combined = [row for org in org_set for row in rank.roster(org)]`.
3. `groups = [(g["label"], len(g["members"])) for g in rank.by_rank(combined)]` — reuses the
   existing ladder sort verbatim, so ordering (ladder then the "Faculty" catch-all) is correct
   by construction, with zero bespoke merge logic.
4. `total = len(combined)`; `with_scholar = sum(1 for r in combined if r["citations"] is not None)`.

**De-dup guard (Fable):** assert `len({r["slug"] for r in combined}) == len(combined)`; if it
fails, a person is homed in two orgs (the multi-home producer regression — known-fragile on
re-crawl) and the headline "N faculty" would silently inflate. Fail loud (or log + de-dup by
slug) so a regression surfaces instead of shipping a wrong number. Today YWCC passes
(119 == 119, verified).

The **chairs list** is derived from the same combined roster: every `rank_index == 0` member
(the "Department Chair" group), returned as `{slug, name, title, dept_name}` so each chair card
is labeled by department. **Zero chairs in a dept → that dept simply contributes none (fine).
Multiple "Department Chair"-titled people (co-chairs / interim overlap) → render all, ordered
by normalized surname** (today exactly one per dept: Oria/Geller/Halper).

### 4.2 Render layer (`render.py` + `templates/hub.html`)

**Reuse, don't reinvent.** The leadership/chair person cards use the **existing**
`row_dir` + `photo_thumb` macros from `leaderboard.html`. Extraction plan (corrected per
senior-eng — `photo_thumb` has **three** call sites in `leaderboard.html`: `row_dir`,
`row_cite`, `row_rising`, so it cannot just be "moved"):
- Move `photo_thumb` **and** `row_dir` into `templates/_person_card.html`.
- `leaderboard.html` does `{% from "_person_card.html" import photo_thumb, row_dir %}` at top;
  its still-local `row_cite` / `row_rising` macros then reference the **imported** `photo_thumb`
  (Jinja macro-scope note: an imported macro referenced by a sibling local macro must be in the
  template's namespace at call time — the top-level `{% from ... import %}` provides that).
- `hub.html` does the same `{% from ... import row_dir %}`.
This is a pure refactor: department leaderboard output (all four views) stays byte-identical,
locked by the EXISTING `test_render.py` rank/citations/rising golden tests — which must be run
as part of this change (the citations/rising goldens are what catch a broken `photo_thumb`
reference, so name them explicitly in the test plan).

**One `_leadership_row(...)` helper, both populations through it** (senior-eng SHOULD-FIX).
Each leadership/chair person becomes a template row via the existing
`render._lb_row(roster_dict, photo_map)`, so photos, monograms, **research-area chips**
(capped at `_LB_AREA_CHIPS`), and links resolve exactly as on the department pages. To build
the `roster_dict`:
- **Chairs** already come from `rank.roster` rows — they carry `areas`, `title`, `citations`,
  etc. Use directly; override the display `title` to the chair role and set `dept_name`.
- **Dean / associate deans** come from the thin `college_leadership` dict; fetch the rest with
  `db.get_faculty(slug)` (which returns `areas`, photo inputs, etc.) and **let the leadership
  role `title` from `college_leadership` WIN over `get_faculty`'s home-faculty title** — so the
  card reads "Associate Dean for Academic Affairs", not the person's professorial-rank title.
- `_lb_row` tolerates a leadership row with no Scholar metrics: `citations=None` →
  `has_scholar=False` and the numeric fields render "—", but those fields are only shown in the
  `row_cite` macro (citations view); the `row_dir` card the hub uses shows only photo/name/
  title/areas, so missing metrics are invisible there. (Verify `_lb_row` requires nothing that
  a leadership dict can't supply — it reads `slug,name,title,areas,citations,h_index,rank_num`,
  all optional-safe.)

`render_hub(...)` gains optional keyword args (all default `None`/empty, so the NJIT hub —
which passes none — is unaffected):
- `stats` — the `college_rollup` dict (coverage line + ordered `(label, count)` groups).
- `leadership` — `{"dean": [rows], "assoc_deans": [rows], "chairs": [rows]}` (template rows).

`hub.html` renders, after the title, in this order:
1. a **stats** block rendered with the **exact same markup the department page uses** — the
   `glance` block (`leaderboard.html:79-83`): two `.glance-hd` spans (`{total}` faculty,
   `{with_scholar}` on Google Scholar) then one `.glance-g` span per rank group rendered
   `{count} · {label}`. This consumes the identical `stats` dict shape
   (`{total, with_scholar, groups}`) that `rank.college_rollup` produces, so the college
   rollup looks 1:1 identical to a department's rank stats. The `glance` block is extracted
   to a shared partial (`templates/_glance.html`, `{% import %}`ed by both templates) so there
   is one source of truth and no drift; the department output stays byte-identical (golden test).
2. the **existing department entry cards** (`hub-cards`) — **moved up to here, directly under
   the stats and above leadership**, so the page reads as a hub first (owner, 2026-07-09);
3. a **Dean** section (label + card[s]) — omitted if empty;
4. an **Associate Deans** section — omitted if empty;
5. a **Department Chairs** section — each card labeled with its **department**
   ("Computer Science — Vincent Oria"); omitted if empty.
All new blocks are guarded by `{% if %}` so a hub without leadership/stats (e.g. the NJIT hub)
renders exactly as today — just the title + department/college cards. Each leadership section
is wrapped in the same `lb-group` / `lb-group-h` structure the department "By rank" view uses,
so "line (section header) then card(s)" is visually identical to what a visitor already sees.

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
- **Title verbatim** — the displayed leadership title is one of the strings already in
  `attrs.titles`; we SELECT which listed title to show (the "dean" one), we never reword it.
- **Same person once in the count** — home-faculty cap of 1 SHOULD guarantee the rollup total
  equals the distinct-person count, but that cap's producer is known-fragile on re-crawl, so
  `college_rollup` ASSERTS distinct-slug == total rather than trusting the invariant (see §4.1).
- **Leadership ↔ faculty overlap is expected and not duplicative** — a leader is also home
  faculty somewhere (verified: Payton & Wang are home faculty in CS; Wu in Informatics, same
  dept as chair Halper). Dept cards are aggregate COUNTS (not per-person), and each person gets
  exactly one card in their Leadership section, so no one is shown twice within a section. A
  regression test asserts each leader's slug appears exactly once in the leadership markup.

## 6. Testing

New unit tests (pytest, alongside the existing FacultyFolio suite):
- `rank.college_rollup`: verified YWCC numbers (119 total, 76 scholar, exact group counts in
  ladder order); a single-dept college equals that dept's `leaderboard_stats`; **ladder order
  holds even when the first-processed org lacks low-index groups** (construct a college whose
  first-by-slug dept has no "Department Chair" but a later dept does — the concat-then-`by_rank`
  approach must still emit "Department Chair" first); the **de-dup assert fires** when a person
  is homed in two orgs (regression guard); includes the college node's own home faculty.
- `db.college_leadership`: classifies dean vs associate dean; **pins the EXACT rendered `name`
  and `title` strings** for Payton/Wu/Bader against the real KG values ("Brook Wu" not
  "Wu, Brook"; "Associate Dean for Academic Affairs"; "Associate Dean" for Bader) — this is the
  test that would have caught the two title/name blockers; empty when no admin edges;
  associate deans sorted by normalized surname.
- Chair derivation: one chair per dept from the `rank_index == 0` group; a dept with zero
  chairs contributes none; >1 chair renders all, surname-sorted; deptless-college safe.
- `render_hub`: with `stats`+`leadership`, order is stats → departments → Dean → Associate
  Deans → Chairs; chairs labeled by department; cards carry research-area chips; a leadership
  person with no areas (Halper) shows none; each leader's slug appears exactly once in the
  leadership markup; **without** `stats`/`leadership` (NJIT hub call) output is byte-identical
  to today (golden test).
- **Macro-extraction regression (BLOCKER guard):** run the EXISTING `test_render.py`
  citations-view and ★ Rising-view golden tests after the extraction — they exercise
  `row_cite`/`row_rising`, the two macros that reference `photo_thumb`; a broken import raises
  at render time and fails them. Name these tests explicitly in the PR so the reviewer confirms
  they ran (not just the rank-view golden).

Manual / pre-deploy: full build, eyeball `/ywcc/`, confirm links resolve and photos render.

## 6a. Pre-deploy verification (Fable)

This college hub is the site's highest-visibility page and its six leadership people are the
most likely to inspect it. **Before the first deploy, manually eyeball all six leadership
cards' research-area chips** (Payton, Wu, Bader, Oria, Geller, Halper) for the known
`<br>`-parser garbling. A future re-crawl can regress THIS page specifically — note that in the
deploy runbook. (Halper's empty chips are correct/honest, not a defect.)

## 7. Goals checklist (to be filled at PR time — shipped / deferred)

- [ ] Stats block (coverage line + college-wide rank rollup chips)
- [ ] Department entry cards moved **above** leadership (directly under stats)
- [ ] Dean section (person card, with research-area chips)
- [ ] Associate Deans section (person cards, with research-area chips)
- [ ] Department Chairs section (person cards, labeled by dept, with research-area chips)
- [ ] Person cards carry research-area chips, capped/honest-empty like the dept pages
- [ ] Person-card macro shared between leaderboard + hub (no output drift)
- [ ] NJIT hub unaffected (byte-identical)

## 8. Resolved decisions (owner, 2026-07-09)

1. **Chair label** — each chair card in the "Department Chairs" section is labeled with its
   **department** ("Computer Science — Vincent Oria"). The chair row carries `dept_name`.
2. **Rollup presentation** — render the college rollup with the **exact same `glance` markup
   the department pages use** (`{count} · {label}` per rank group). No separate design; "look
   at the department, do the same." Extracted to a shared partial so there is one source.
3. **"2 · Faculty" catch-all chip** (Fable optional nit) — KEPT AS-IS for 1:1 consistency with
   the department pages; renaming it only at college level would create the very drift the
   shared-markup design avoids. Revisit only if it confuses real users.

## 9. Review log (2026-07-09)

**Senior-eng review — "needs rework before build"; all findings resolved in this revision:**
- [BLOCKER] admin `titles` is a multi-item list; extraction unspecified + doc example wrong →
  §4.1 now defines the "show the entry containing 'dean'" rule; §2 example corrected to real
  KG strings.
- [BLOCKER] `college_leadership` names need `normalize_name` → specified in §4.1.
- [BLOCKER] macro extraction missed `photo_thumb`'s 3 call sites → §4.2 now uses
  `{% from ... import photo_thumb, row_dir %}` and keeps `row_cite`/`row_rising` local;
  citations/rising goldens named in §6.
- [SHOULD-FIX] rollup merge fragile → replaced with concat-rosters-then-`by_rank`-once (§4.1).
- [SHOULD-FIX] include the college node itself (deptless colleges) → added to the org set (§4.1).
- [SHOULD-FIX] one explicit leadership-row helper + title precedence → `_leadership_row` (§4.2).
- [NIT] chair 0/>1 handling; leader↔faculty overlap; multiple deans → covered in §4.1 / §5.

**Fable — GO-WITH-CHANGES; both changes incorporated:**
- De-dup invariant assert in `college_rollup` (§4.1).
- Pre-deploy eyeball of the six leadership area chips (§6a).
- Optional "2 · Faculty" rename → declined for consistency (§8.3).

Both reviews to be re-run (or the senior-eng reviewer re-consulted) against this revised spec
before the build begins, per the owner's expert-review hard gate.

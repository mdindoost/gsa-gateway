# FacultyFolio Funding v2 — Awards & Counts, No Dollars (delta spec)

**Date:** 2026-07-10 · **Status:** design, pending Fable review + owner approval
**Supersedes the display layer of:** `2026-07-10-facultyfolio-funding-rendering-design.md` (v1, LIVE).
**Type:** delta spec — modifies the just-shipped v1 render/rollup; **no data-model or capture change.**

## Why

Owner decision (2026-07-10): dollar figures on faculty/dept/college pages feel wrong — they read as a
money ranking, and federal-only NSF+NIH dollars misrepresent scale (training/scholarship grants inflate;
DOD/industry funding invisible). Replace **dollars** with **award names + activity counts**, and show only
**PI-led** work (no co-PIs) so the page never over-claims participation.

## What changes (display + rollup only — the `attrs.funding` bags are untouched)

### 1. Profile "Research funding" section (`render.funding_view` + `templates/profile.html`)
Grouped **NSF then NIH**, section absent when the person has no PI-led award in either agency.

- **NSF group** — include only `awards[]` with `at_njit=True` (these are already lead-PI only; NSF's
  per-PI search returns an award under its Principal Investigator). **Drop all dollar output.**
  - Summary line: `"{n} NSF award{s} (as Principal Investigator)"`.
  - Each row: verbatim **title** linked to `https://www.nsf.gov/awardsearch/showAward?AWD_ID={id}`;
    meta `NSF {id}`; **year range** `{startYear} – {expYear}`; **Active** chip when `exp` date `>= today`.
- **NIH group** — include only `projects[]` with `role == "contact"`. **Drop co-PI projects entirely**
  (remove the co-PI rows, the `copi` chip, and the "co-investigator on N projects" variant). **Drop all
  dollar output.**
  - Summary line: `"{m} NIH project{s} (as Contact PI)"`.
  - Each row: verbatim **title** linked to `https://reporter.nih.gov/project-details/{appl_id}` (plain
    span when `appl_id` missing); meta `NIH {core}`; **FY range** `FY{fy_first} – FY{fy_last}`;
    **Active** chip when `fy_last >= fy_now` (`fy_now = today.year+1 if today.month>=10 else today.year`).
- Ordering unchanged: recency-first (NSF by exp desc; NIH by fy_last desc), missing-date last.
- Provenance line unchanged: `"From {NSF and NIH|NSF|NIH} public award records · as of {date_long}"`.
- **Role wording is the honest-labeling anchor:** "(as Principal Investigator)" / "(as Contact PI)"
  makes explicit that the list is PI-led work, not all participation.

`funding_view` return shape changes: each row drops `amount`/`unit`/`copi`; keeps
`title`/`url`/`meta`/`years`/`active`. Each group's `summary` becomes the **count** string above.

### 2. Aggregate rollup (`rank.funding_rollup` + `render._rollup_view` + both templates)
Replace dollar totals with **counts**, per agency, never combined:

`"{nsf_awards} NSF awards ({nsf_active} active) · {nih_projects} NIH projects ({nih_active} active) · {funded} funded faculty"`

- `funding_rollup(org_ids)` returns
  `{nsf_awards, nsf_active, nih_projects, nih_active, funded, as_of}` or `None` when the subtree has no
  PI-led awards.
- Counting (dedup by **person node id**, skip `config.SUPPRESSED`):
  - `nsf_awards` = total count of `at_njit` NSF awards across the subtree's faculty. **No award-ID dedup
    needed** — each NSF award lives in exactly one person's bag (its lead PI); a person is counted once
    via the id-dedup, so their awards are counted once. (Verified 2026-07-10: 0/92 at_njit NSF awards
    appear in >1 bag university-wide.)
  - `nih_projects` = count of `role == "contact"` NIH projects across the subtree's faculty.
  - `nsf_active` / `nih_active` = subset that is currently active (NSF `exp >= today`; NIH `fy_last >=
    fy_now`). Active is computed per award/project the same way as the profile chip.
  - `funded` = distinct persons with ≥1 PI-led award (an `at_njit` NSF award OR a contact NIH project).
    **Definitional shift from v1** (v1 used `njit_total > 0`): live-equivalent today (verified: 0
    zero-obligated at_njit NSF rows, 0 zero-cost contact NIH rows → 36 = 36), but the count-based
    definition is the correct one for v2 and diverges only if a $0 PI-led award ever appears. Note it so
    a reviewer diffing v1 doesn't read it as a count bug.
  - `as_of` = min `updated_at` among the contributing bags (kept from v1).
- **Determinism:** `funding_rollup` and `funding_view` take an injectable `today` (default
  `datetime.date.today()`) so active-count tests are stable; the count tests pin `today=2026-07-10`.
  The total award/project + funded counts are date-independent and can be asserted unconditionally;
  the *active* subset asserts against the pinned date.
- `_rollup_view` maps the raw rollup dict to a template-ready view-model. **Contract:** return
  `{parts: [(count_label, agency)], funded, as_of}` where each `parts` entry is a pre-rendered clause
  string like `"59 awards (14 active)"` paired with its agency (`"NSF"`/`"NIH"`) — mirroring v1's
  `parts` list shape so the template edit is minimal (v1 was `($amount, agency)`; v2 is
  `(count_label, agency)`). Omit an agency's part when its count is 0; keep `funded`; `None`/all-zero
  rollup → `None` (no line). `test_rollup_render.py` asserts this `parts` shape.

### 3. Honest-labeling constraints (updated)
- **No `$` in any funding output** — the v1 rule "`$` only inside `.rollup`" becomes "**no `$` in the
  generated funding scaffolding**" (summaries, meta lines, year/FY ranges, rollup counts). **Scope the
  invariant assert to the scaffolding, NOT the verbatim award title** (`.fund-t` text): 0 live NSF/NIH
  titles contain `$`, but a future verbatim title could, and the verbatim hard line forbids stripping
  it — so the test strips/excludes `.fund-t` title text before asserting no `$`, rather than failing on
  authoritative title content.
- NSF and NIH counts are shown **separately, never summed** into one number.
- Funding still appears **only** in the profile `#funding` section and the aggregate `.rollup` line —
  never per-person on hero/glance/card/leaderboard-column.
- Titles rendered **verbatim**, each linked to its government record.
- Role wording ("as Principal Investigator" / "as Contact PI") present on every summary.

### 4. Files touched
- `facultyfolio/render.py` — `funding_view` (drop dollars, filter NIH to contact, count summaries, PI
  wording); `_rollup_view` (counts).
- `facultyfolio/rank.py` — `funding_rollup` returns counts (+ active + funded), computes active per award.
- `facultyfolio/templates/profile.html` — funding rows drop the dollar cell; keep title/meta/years/chip.
- `facultyfolio/templates/hub.html` + `leaderboard.html` — `.rollup` line renders the count string.
- `facultyfolio/assets/style.css` — `.fund-cite` dollar styling removed/repurposed (row layout without
  the big dollar figure); `.rollup` count styling.
- `facultyfolio/format.py` — `money` / `money_exact` become **unused** by funding; remove them and their
  test (`test_format_money.py`) unless another caller exists (grep first). `date_long`/`month_year`/
  `_MONTHS` stay (used by provenance + rollup as_of).
- Tests — update:
  - `test_funding_view.py` (rows drop amount/unit/copi; count summaries with PI wording; NIH contact-only),
  - `test_profile_funding_render.py` (**currently asserts `"obligated"`/`"costs"` — those words are
    deleted; rewrite to assert the count-summary wording + gov-link presence + **no `$`** in the section**),
  - `test_funding_rollup.py` (count assertions vs the live YWCC/CS/DS targets below),
  - `test_rollup_render.py` (count `parts`, not dollar parts),
  - `test_funding_invariants.py` (no `$` anywhere in the funding section/rollup — see §3; NIH group has no
    co-PI chip).
  - **RETAINED UNCHANGED (do NOT delete):** `test_db_funding.py` and the
    `test_njit_total_equals_sum_of_contributing_rows` tripwire in `test_funding_invariants.py`. The
    `attrs.funding` bags (including `njit_total`) are untouched by v2 — the number is simply never
    displayed — so the enrichment-drift tripwire stays valid and must survive the dollar-display removal.

## Live-data expectations (for the new count tests — computed 2026-07-10, today=2026-07-10)
Concrete targets the tests assert (re-derive at build time; if they drift, the data changed):
- **CS (org 16):** NSF **59 awards (14 active)** · NIH **5 projects (1 active)** · **23 funded faculty**.
- **YWCC (college = depts + college node):** NSF **92 awards (25 active)** · NIH **5 projects (1 active)**
  · **36 funded faculty**. (All 92 at_njit NSF awards + all 5 contact NIH projects are within YWCC — the
  only college enriched so far.)
- **data-science:** NSF **17 awards (8 active)** · NIH **0** · **7 funded faculty**.
- **NIH is unaffected by the co-PI drop in practice** — every live YWCC NIH project is already `contact`
  (0 co-PI), so no NIH row disappears; the change is purely that the co-PI *code path* is removed. NIH
  active = 1 (Wei's R35GM158529, `fy_last`=2026 ≥ FY2026); the rest have older `fy_last`.

## Goals checklist (fill at PR)
- [ ] No dollars anywhere (profile + rollup) — invariant-tested.
- [ ] NSF PI-only + NIH contact-only; co-PI code paths removed.
- [ ] "as Principal Investigator" / "as Contact PI" wording on summaries.
- [ ] Profile rows keep title (linked) + award/core # + year/FY range + Active chip.
- [ ] Rollup shows per-agency award counts + active counts + funded-faculty count; never summed.
- [ ] Counts correct vs live DB (no award double-count; person-dedup).
- [ ] Money helpers removed if unused; suite green; rebuilt + spot-checked before deploy.

## Non-goals / deferred
- Co-PI capture/display (owner: not shown). If ever revisited, it reopens award-ID dedup.
- Any change to `funding_enrich` data capture — none needed.

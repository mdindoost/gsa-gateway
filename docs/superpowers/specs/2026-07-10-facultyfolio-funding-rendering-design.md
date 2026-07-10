# FacultyFolio — Research Funding Rendering Design

**Date:** 2026-07-10 · **Status:** approved (mockup signed off) · **Depends on:** the NSF+NIH
funding data already live in the KG (`scripts/funding_enrich.py`, see
`docs/superpowers/findings/2026-07-10-federal-funding-enrichment.md`).

## Goal

Render each faculty member's NSF + NIH research funding on their FacultyFolio profile, plus a single
funding rollup line on the department and college hubs — reusing the existing visual grammar, honest
per-agency labeling, and no cross-person comparison surfaces.

This is **almost** rendering-only: it reads the `attrs.funding.{nsf,nih}` bags that already exist. The
one data-layer prerequisite is **Task 0** below (capture NIH `appl_id` so each NIH row can link to its
real RePORTER record); no new data source is added.

## Task 0 (prerequisite) — capture NIH `appl_id` for record links

RePORTER has no stable URL from a core project number, but every project-year row carries an integer
`appl_id`, and `https://reporter.nih.gov/project-details/{appl_id}` is the real per-project page
(verified live). Add `ApplId` to `funding_enrich.py`'s NIH `include_fields`, and store — per core
project — the `appl_id` of its **latest** fiscal-year row (that page shows the whole project history):
`projects[].appl_id`. Then re-run `funding_enrich.py --org ywcc --source nih --commit` (gated,
idempotent; adds the field to the 2 existing NIH people). Without this, invariant #4 ("every row links
to the government record") cannot hold for NIH.

## Data consumed (already present in the KG)

```
attrs.funding.nsf = {updated_at, njit_total, matched_by,
                     awards:[{id, title, awardee, start, exp, obligated, at_njit}]}
attrs.funding.nih = {updated_at, njit_total, matched_by:"org+name",
                     projects:[{core, title, total, role, fy_first, fy_last, appl_id}]}   # appl_id via Task 0
```
- NSF `start`/`exp` are `MM/DD/YYYY` strings; `obligated` is int dollars; `at_njit` bool.
- NIH `fy_first`/`fy_last` are int years; `total` int dollars; `role` ∈ {"contact","co_pi"}.

## Placement

A new `<section>` on the profile, **immediately after "Selected work"** (id=`pubs`) and before
"Awards & honors". Section is **absent entirely** when the faculty member has neither
`funding.nsf` nor `funding.nih` (never print "no funding").

## Profile section — structure

Reuses the established grammar: `.eyebrow` → `h2` → `.rule`, then a muted provenance line, then one
group per source. Each group reuses the `.pub` row pattern (a fixed-width numeric left block + a
title/meta main block). New CSS classes `.fund-*` derive from `.pub-*`.

```
eyebrow:  "Sponsored research"
h2:       "Research funding"
provenance (muted mono): "From {NSF and NIH | NSF | NIH} public award records · as of {updated_at}"
```
- Provenance names ONLY the sources actually present (both / NSF / NIH). The `as of` date is the
  **older** of the two `updated_at` values when both are present.

### Per-agency group

```
group summary line:
  NSF:  "NSF awards"    — right-aligned: "{$njit_total exact} obligated · {n} award{s}"
  NIH:  "NIH projects"  — right-aligned: "{$njit_total exact} project costs · {n} projects (as contact PI)"
```
- `n` counts only the rows that contribute to `njit_total` (NSF: `at_njit=true`; NIH:
  `role="contact"`).
- Then one `.fund-row` per award/project.

### Row

```
left block (.fund-cite):  {dollar}         (big, display face)
                          {unit word}      (mono, uppercase: "obligated" NSF / "costs" NIH)
main (.fund-main):        {title}          (verbatim, linked to the official record)
                          {meta line}      "{NSF <id> | NIH <core>} · {years} [· Active]"
```
- **Title is verbatim** (crawl hard line — no case-fixing, no prefix-stripping). Linked:
  - NSF → `https://www.nsf.gov/awardsearch/showAward?AWD_ID={id}`
  - NIH → `https://reporter.nih.gov/project-details/{appl_id}` (from Task 0; the real project page).
- **Years:** NSF `{start year} – {exp year}`; NIH `FY{fy_first} – FY{fy_last}`.
- **Active chip** (green `.chip.active`, text "Active") when the grant is still running:
  - NSF: **parsed `exp` date `>=` today** (parse `MM/DD/YYYY` to a date, compare to today — NOT a
    year comparison; an `exp` of 04/30/2026 is expired mid-2026). Missing/unparseable `exp` → no chip.
  - NIH: `fy_last >=` the current **federal** fiscal year, where `fy_now = year + 1 if month >= 10
    else year` (federal FY starts Oct 1 — a bare calendar-year proxy mislabels Oct–Dec).
- **co-PI rows** (NIH `role="co_pi"`): rendered in-list **after** all contact rows, with the dollar
  block muted (`.fund-row.copi`), a muted `co-PI` chip in the meta line, and **excluded from
  `njit_total`** (the summary already says "as contact PI", so the exclusion is self-evident). No
  separate sub-list. The muted dollar is the **whole project's** cost (mostly not the person's share)
  — acceptable as the public project figure; noted so it isn't re-litigated.
- **NIH group with 0 contact rows** (co-PI only): the "$X project costs · N projects (as contact PI)"
  summary would read "$0 · 0 projects" — absurd. Instead print a plain summary line
  **"co-investigator on {N} project{s}"** (no dollar figure), then the co-PI rows.

### Ordering within a group

Recency first — by end date descending (NSF `exp`, NIH `fy_last`), dollars descending as tiebreak.
NIH contact-PI rows always before co-PI rows. A missing/unparseable end date sorts **last within its
role block** (dollar tiebreak still applies).

### Dollar formatting (`format.py` helper)

- `< $1,000,000` → exact, comma-grouped: `$327,808`.
- `>= $1,000,000` → compact, two decimals + `M`: `$4.58M`, `$1.65M`.
- **Group summary lines always exact** (`$1,653,383`) — the precise figure reads official and is the
  one people quote.

### Omissions / empty states (adaptive)

- No funding at all → section absent.
- One source only → render only that group; no placeholder for the missing one.
- All NSF awards `at_njit=false` (so `njit_total=0`) → treat as no NSF group.
- **Prior-institution NSF awards** (`at_njit=false`) are **not shown** in v1 (data preserved for a
  possible future "earlier awards at {awardee}" footnote — count only, out of scope here).

## Funding rollup (department leaderboards + college/root hubs)

**Surface map (important — dept pages are NOT hubs):**
- **Department pages** are *leaderboards* → `templates/leaderboard.html`, `render.render_leaderboard`,
  built by `build.build_dept`. The rollup line sits under the `lb-glance` strip.
- **College hub + NJIT root** → `templates/hub.html`, `render.render_hub`, built by
  `build.build_college_hub` (college) and the root build. The rollup sits under the existing rank
  rollup. The NJIT root hub gets it too — a `{% if funding_rollup %}` guard makes that free.

Computed at **build time** via a shared `rank.funding_rollup(org_ids)` (org rollups already live in
`rank.py` — honors ingestion-vs-serving; funding is never precomputed in the enrichment layer).

```
.rollup:  "Sponsored research  {$X}M NSF · {$Y}M NIH · {n} funded faculty · as of {Mon YYYY}"
```
- **Org set:** dept = that dept's home faculty; college = its dept children **+ the college node
  itself** (mirrors `college_rollup`/`college_coverage`); **dedup by person node id** (a dup-home
  person is counted once — dedup, do NOT assert-zero like `college_rollup` does). College total is
  therefore `>=` the sum of its dept rollups (equal for YWCC today, where no faculty are college-homed).
- Sum `njit_total` per source across that set. `n` (**"funded"**) = distinct persons with
  `njit_total > 0` on **either** agency (same contributing rule as the profile — a co-PI-only or
  all-`at_njit=false` person does not count).
- **Never summed into one figure.** One shared `money()` (two-decimal `$X.XM`) for both profile and
  hub. **Adaptive:** omit an agency term when its subtree total is `$0` (Data Science → NSF only).
- **`as of`:** the **oldest** `updated_at` among the counted bags, shown as `Mon YYYY` (aggregate
  view). (The per-profile provenance shows the full date `Jul 10, 2026`; this coarser aggregate date
  is intentional and stated, not an inconsistency.)
- Nothing per-person on any hub/leaderboard — no "top funded" list, no funding column, no percentile.
  The rollup line is the **only** funding surface off the profile.

## Honest-labeling constraints (hard)

1. NSF and NIH shown **separately, never summed** — different measures (NSF obligated-to-date vs NIH
   FY-summed costs) and an incomplete set (no DOD/DARPA/foundation).
2. Funding is **federal NSF+NIH only** — the provenance line bounds the claim; never labeled "total
   funding".
3. Funding appears **only** inside the profile section and the hub rollup line — **never** on the
   hero, glance/person cards, leaderboards, or any surface where faculty appear side by side. This is
   the structural answer to low-number-next-to-high-number embarrassment.
4. Every row links to the government record (self-auditing against a name-match error).

## Files

- **`scripts/funding_enrich.py`** (Task 0) — add `ApplId` to NIH `include_fields`; store
  `projects[].appl_id` (latest-FY row per core). Gated re-run.
- **`facultyfolio/db.py`** — `get_faculty` extracts named fields (it does NOT return raw `attrs`); add
  one line to the returned dict: `"funding": attrs.get("funding") or {}`.
- **`facultyfolio/format.py`** — no money helper exists today (only `commafy`); add `money(n)`
  (exact `<$1M` via `commafy`, compact `$X.XM` `>=$1M`) + `money_exact(n)` (always `commafy`).
- **`facultyfolio/render.py`** — `funding_view(f)` builds the profile view-model (per-source groups,
  ordered rows, `active` flags, links, unit words, co-PI-only summary variant); returns `None` when
  no contributing rows. Wire into `render_profile` ctx as `funding`. Also add `funding_rollup` params
  to **`render_leaderboard`** and **`render_hub`** signatures (view-model: the two `$M` strings, the
  count, the `Mon YYYY` date; `None` when the subtree has no funding).
- **`facultyfolio/rank.py`** — `funding_rollup(org_ids)` → `{nsf, nih, n_funded, as_of}` per the Org
  set + rules above (dedup by person id). Mirrors `college_rollup`.
- **`facultyfolio/build.py`** — compute `funding_rollup` in **`build_dept`** (→ `render_leaderboard`)
  and **`build_college_hub`** + the root build (→ `render_hub`), and pass it through. *(Omitting this
  file silently ships a college-only rollup and drops the dept goal.)*
- **`facultyfolio/templates/profile.html`** — new `{% if funding %}` section after `#pubs`.
- **`facultyfolio/templates/leaderboard.html`** — rollup line under the `lb-glance` strip
  (`{% if funding_rollup %}`).
- **`facultyfolio/templates/hub.html`** — rollup line under the rank rollup (`{% if funding_rollup %}`;
  also covers the NJIT root hub).
- **`facultyfolio/assets/style.css`** — `.fund-*`, `.chip.active`, `.chip.copi`, `.rollup` (derived
  from `.pub-*` / existing chip styles; mobile breakpoint mirrors `.pub`).

## Testing

Against live data unless noted:
- `funding_view`: Zhi Wei renders both groups (NSF $327,808 obligated · 1; NIH $1,653,383 costs · 2);
  Oria NSF-only $9,076,163 · 3 (NIH group absent); Perl NIH-only (NSF absent); a no-funding person →
  `None`; recency ordering (Zhi Wei's FY2025–26 NIH row before FY2021–24); active-chip (Zhi Wei's R35
  Active, R15 not; his NSF award expired → no chip); dollar formatting ($4,078,362 → `$4.08M`,
  $327,808 exact, summary always exact).
- **co-PI (SYNTHETIC fixture — 0 co_pi rows exist live):** a hand-built `funding.nih` with a `co_pi`
  row asserts it renders muted + tagged, sorts after contact, is excluded from `njit_total`, and the
  co-PI-only case prints "co-investigator on N projects" (no `$` summary).
- `funding_rollup`: YWCC (depts 16/73/100 + college node) = NSF `$37,401,075` / NIH `$6,076,611` /
  `36`; Data Science subtree → NIH term omitted (`$0`).
- **Tripwire:** for every funded person, stored `njit_total` == Σ contributing rows (NSF `at_njit`,
  NIH `contact`) — catches future enrichment drift.
- **Honest-labeling invariant (assert on `$` + class names, NOT the token "NSF" — crawled award/honor
  titles legitimately contain "NSF"):**
  - **Per-person surfaces** — the rendered `glance()` (`_glance.html`), `row_dir()`
    (`_person_card.html`), and the profile hero aside — contain **no `$` and no `.fund-`/`.rollup`**.
  - **Full leaderboard + hub pages** — `.fund-` (the profile funding classes) **never appears**, and
    `$` appears **only inside the `.rollup` element** (the aggregate rollup is allowed; a per-person
    funding column is not). Asserting the whole page — not just the macros — closes the hole of a
    funding column added directly in the template. (The aggregate rollup carrying `$` is the intended
    exception; per-person funding on a comparison surface is the thing forbidden.)

## Verification

- `pytest facultyfolio/tests/ -q` green.
- Real-build spot check: rebuild Zhi Wei, Oria, Perl, a no-funding person, the CS **leaderboard** +
  YWCC **hub** + NJIT **root**; eyeball against the approved mockup and click one NSF + one NIH link.

## Goals checklist (shipped / deferred)

- [ ] Task 0: NIH `appl_id` captured; NIH rows link to `project-details/{appl_id}`
- [ ] Profile "Research funding" section after Selected work, NSF+NIH stacked, adaptive/absent-when-empty
- [ ] Per-agency summary + rows (dollar+unit, verbatim linked title, meta, Active chip w/ correct date/FY rules)
- [ ] co-PI in-list, muted, tagged, excluded from total; co-PI-only summary variant
- [ ] Recency-first ordering (missing-date last); dollar formatting (exact/compact/summary-exact)
- [ ] Rollup on dept **leaderboards** + college/root **hubs** (build.py wired; two numbers, never summed, $0 agency omitted, dedup by person)
- [ ] Honest-labeling invariants incl. no funding on any comparison surface (tested via `$`/class asserts, full leaderboard page)
- **Deferred (loudly):** prior-institution NSF footnote; OpenAlex-grants "funder breadth" line (owned
  by the OpenAlex build, not this spec).

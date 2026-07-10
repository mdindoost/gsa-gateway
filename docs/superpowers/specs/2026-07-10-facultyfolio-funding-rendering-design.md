# FacultyFolio — Research Funding Rendering Design

**Date:** 2026-07-10 · **Status:** approved (mockup signed off) · **Depends on:** the NSF+NIH
funding data already live in the KG (`scripts/funding_enrich.py`, see
`docs/superpowers/findings/2026-07-10-federal-funding-enrichment.md`).

## Goal

Render each faculty member's NSF + NIH research funding on their FacultyFolio profile, plus a single
funding rollup line on the department and college hubs — reusing the existing visual grammar, honest
per-agency labeling, and no cross-person comparison surfaces.

This is a **rendering-only** change. It reads the `attrs.funding.{nsf,nih}` bags that already exist;
it writes nothing to the KG and adds no new data source.

## Data consumed (already present in the KG)

```
attrs.funding.nsf = {updated_at, njit_total, matched_by,
                     awards:[{id, title, awardee, start, exp, obligated, at_njit}]}
attrs.funding.nih = {updated_at, njit_total, matched_by:"org+name",
                     projects:[{core, title, total, role, fy_first, fy_last}]}
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
  - NIH → `https://reporter.nih.gov/search/{core}/projects` (a stable per-project detail URL needs
    RePORTER's `appl_id`, which the enrichment does not currently store — a search on the core
    number is the v1 link; capturing `appl_id` in `funding_enrich` is a noted follow-up).
- **Years:** NSF `{start year} – {exp year}`; NIH `FY{fy_first} – FY{fy_last}`.
- **Active chip** (green `.chip.active`, text "Active") when the grant is still running:
  - NSF: `exp` year `>=` current year (parse the `MM/DD/YYYY`; compare full date to today).
  - NIH: `fy_last >=` current fiscal year (use current calendar year as the FY proxy).
- **co-PI rows** (NIH `role="co_pi"`): rendered in-list **after** all contact rows, with the dollar
  block muted (`.fund-row.copi`), a muted `co-PI` chip in the meta line, and **excluded from
  `njit_total`** (the summary already says "as contact PI", so the exclusion is self-evident). No
  separate sub-list.

### Ordering within a group

Recency first — by end date descending (NSF `exp`, NIH `fy_last`), dollars descending as tiebreak.
NIH contact-PI rows always before co-PI rows.

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

## Hub rollup (department + college)

One line under the existing rank rollup on each hub, computed at **build time** in `rank.py` (where
org rollups already live — honoring the ingestion-vs-serving separation; funding is never
precomputed in the enrichment layer).

```
.rollup:  "Sponsored research  {$X}M NSF · {$Y}M NIH · {n} funded faculty · as of {Mon YYYY}"
```
- Sum `njit_total` across the org subtree's home faculty per source; `n` = distinct faculty with any
  funding. Two numbers, **never summed into one**. Compact `$M` format.
- **Adaptive:** omit an agency term when its subtree total is `$0` (e.g. Data Science shows only NSF).
- Nothing per-person on the hub — no "top funded" list, no funding column, no percentile. The rollup
  is the only funding surface on a hub.

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

- **`facultyfolio/db.py`** — `get_faculty` already returns `attrs`; expose `funding` (the raw
  `attrs.funding` dict) in the returned faculty dict.
- **`facultyfolio/format.py`** — add `money(n)` (exact/compact) + `money_exact(n)`.
- **`facultyfolio/render.py`** — `funding_view(f)` builds the template view-model: per-source groups
  with formatted summaries, ordered rows (recency, co-PI last), `active` flags, links, unit words;
  returns `None` when no fundable rows. Wire into `render_profile` ctx as `funding`.
- **`facultyfolio/rank.py`** — `funding_rollup(org_ids)` → `{nsf, nih, n_funded}` summed across the
  subtree's home faculty (dedup by person). Reuses the existing roster helpers.
- **`facultyfolio/templates/profile.html`** — new `{% if funding %}` section after `#pubs`.
- **`facultyfolio/templates/hub.html`** — rollup line under the rank rollup (`{% if funding_rollup %}`).
- **`facultyfolio/assets/style.css`** — `.fund-*`, `.chip.active`, `.chip.copi`, `.rollup` (derived
  from `.pub-*` / existing chip styles; mobile breakpoint mirrors `.pub`).
- **Tests** (`facultyfolio/tests/`) — against live data:
  - `funding_view`: Zhi Wei renders both groups; Oria NSF-only; a no-funding person → `None`;
    co-PI excluded from total but present; recency ordering; active-chip logic; dollar formatting
    (exact <$1M, compact ≥$1M, summary exact).
  - `funding_rollup`: YWCC = NSF $37,401,075 / NIH $6,076,611 / 36; Data Science NIH term omitted ($0).
  - honest-invariant test: no funding token appears in the glance/person-card render or hero.

## Testing / verification

- `pytest facultyfolio/tests/ -q` green.
- Real-build spot check: rebuild Zhi Wei, Oria, a no-funding person, the CS + YWCC hubs; eyeball
  against the approved mockup.

## Goals checklist (shipped / deferred)

- [ ] Profile "Research funding" section after Selected work, NSF+NIH stacked, adaptive/absent-when-empty
- [ ] Per-agency summary + rows (dollar+unit, verbatim linked title, meta, Active chip)
- [ ] co-PI in-list, muted, tagged, excluded from total
- [ ] Recency-first ordering; dollar formatting (exact/compact/summary-exact)
- [ ] Dept + college hub rollup line (two numbers, never summed, per-agency $0 omitted)
- [ ] Honest-labeling invariants incl. no funding on any comparison surface (tested)
- **Deferred (loudly):** prior-institution NSF footnote; NIH `appl_id` capture for exact project
  links (v1 links to a RePORTER core-number search); OpenAlex-grants "funder breadth" line (owned by
  the OpenAlex build, not this spec).
```

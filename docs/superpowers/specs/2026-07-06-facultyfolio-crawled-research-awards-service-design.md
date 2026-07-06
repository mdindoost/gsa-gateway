# FacultyFolio — Surface crawled Research-statement, Awards, and Service (delta-spec)

**Status:** DRAFT — for senior-eng + data-fidelity review, then owner approval.
**Base spec:** `2026-07-03-facultyfolio-generator-design.md` (this is a rev-5 delta to §3 trust
boundary + §4 page model; everything else in the base spec stands).
**Author:** (session 2026-07-06) · **Owner:** Mohammad

---

## 1. Why (the mistake we're correcting)

The base spec §3 (trust boundary) locked publishable prose to
`created_by='crawler' AND type IN ('education','teaching','profile')`, and §12 stated outright
*"Awards/recognition data (not crawlable — permanent claim-hook for now)."* The build faithfully
implemented that: `db.py _PROSE_TYPES=("education","teaching","profile")`, research areas from
`researches` edges only, Recognition is a permanent claim-hook.

**That data assumption is factually wrong.** It was written against the two hand-built samples
(Koutis, Kieran), which happened to carry no `award`/`research_statement`/`service` rows — the exact
"ground on live rows, not the pretty samples" trap the base spec's post-review note already flagged.
Live rows show these types DO exist and ARE `created_by='crawler'`:

| type | CS faculty with ≥1 active crawler row (of 57) |
|------|-----|
| `research_statement` | **28** |
| `award` | **6** |
| `service` | **4** |

Concretely, Shantanu Sharma's page shows *"Awards aren't in our public data yet… not crawlable"*
and *"Research areas aren't listed"* while the DB holds 9 crawled `award` rows + a crawled
`research_statement` for him. This delta surfaces that already-crawled data.

**In scope of the hard lines:** all three types are `created_by='crawler'` NJIT prose → governed by
the verbatim / mechanical-only hard lines. No LLM prose (`type='about'` stays HARD-EXCLUDED). No
curation dictionaries. Scholar is NOT involved (the affected faculty mostly have no Scholar account).

## 2. Goals (checklist — every one ships or is loudly deferred)

- **G1 — Research union + statement fallback.** Research section shows area chips from the UNION of
  `researches` edges + `research_areas` KB items (reusing the bot's `entity.research_of_person`
  logic), AND renders the `research_statement` prose (mechanically de-prefixed) when present. Edge-less
  faculty who have a statement (Sharma, fh224) stop showing the false "not listed" hook.
- **G2 — Awards → Recognition.** Recognition renders the crawled `award` rows (verbatim, mechanically
  de-noised) instead of the permanent claim-hook, for the 6 who have them. The other 51 keep the
  positive claim-hook (unchanged).
- **G3 — Service section.** A Service block renders the crawled `service` prose (de-prefixed) for the
  4 who have it; omitted (no hook) for those who don't — it is NOT one of the "always render" sections.
- **G4 — Trust-boundary amendment.** §3.1 publishable set extended to add
  `award`, `service`, `research_statement` (still `created_by='crawler'`, still mechanical/verbatim).
- **G5 — Honest-empty preserved.** The 15 CS faculty with genuinely no research data anywhere still
  render the honest claim-hook. No fabrication, no cross-person bleed.

## 3. Design (REVISED after senior-eng + data-fidelity review — see §7)

**Key revision:** do NOT reuse `entity.research_of_person` — it unions `created_by='scholar'`
self-asserted areas with no filter (193 scholar rows live), which would leak Scholar interests onto
NJIT-authoritative pages and breach the crawler-only boundary (data-fidelity BLOCKER 1). Instead keep
the area path **crawler-only**, entirely inside `db.py`. Verified: edges-only already captures every
crawler area for 56/57 CS faculty (only meh43 has one crawler `research_areas` area edges miss), so
this is a tiny, safe addition — and crix/gotsman/mili correctly stay honest-empty exactly as today.

### 3.1 Data layer (`db.py`) — crawler-only, single reader
- **Areas (unchanged path + one safe addition):** keep the current `researches`-edges read, and ALSO
  union in `research_areas` KB items filtered `created_by='crawler'` (picks up meh43's "extreme value
  theory"). Dedup with db.py's existing `_area_key` (strips punctuation → also fixes Oria's garbled
  `"Recommender Systems..."` / `"Spatio-temporal"` dup chips). NO scholar rows, ever.
- **`research_statement`** (new field): read the crawler row, mechanically de-prefixed (3.3), or None.
- **`awards`** (new field): read from the **`title` column** (verified clean verbatim award strings;
  the noise rows are `title="2019"`), de-noised (3.3). Not `content` (which carries the lead-in).
- **`service`** (new field): the crawler `service` row, de-prefixed (3.3), or None.
- Extend the `_prose` trust filter to the new types (still `created_by='crawler'` only).

### 3.2 Render + templates
- **Research section:** area chips (as today) + a `research_statement` prose block below them when
  present, labelled like About (`Crawled from the NJIT department profile · not written or generated`).
  Hook shows ONLY when BOTH areas and statement are empty (fixes Sharma).
- **Recognition section:** if `awards` non-empty → render the list (verbatim strings); else the existing
  positive claim-hook (unchanged). Fixed heading stays.
- **Service section:** new section, rendered only when `service` present (omit silently otherwise — it
  is a rows-are-facts block, not an always-render claim section, so no hook, no empty-state budget hit).

### 3.3 Mechanical formatters (Option A — no lookup tables; run on unseen strings)
- **Award de-noise (on the `title` column):** drop any `award` row whose **title** matches
  `^\s*\d{4}\s*$` (a bare year split from its award by the crawler's 2-column table parse — Sharma
  1790/1792/1794). Strip trailing `,`/whitespace from kept titles; otherwise verbatim. Order by
  descending leading year when present, else source order (stable → idempotent). No prefix strip needed
  (the `title` column has no `"Award received by…"` lead-in — verified). Data-safe: every bare-year row
  is an orphan whose year already appears in a sibling full-award row (both reviewers verified, all 6
  faculty + others; no real award lost).
- **Prefix strip (statement + service) — dept-optional, name-agnostic (strip-to-first-colon):**
  `^(Research statement|Service) (of|by) [^:]{1,160}:\s*` — matches the proven `format.py` pattern
  (`format_education` uses `^Education of .*?:\s*`). Dept parenthetical is OPTIONAL (72% of statement
  rows / 61% of service rows repo-wide have none; CS-57 all have it, but this must survive expansion).
  First-colon is safe: a name/dept can't contain `:`; the human text's own colons come after. Remaining
  text passes through UNCHANGED (verbatim — no summarize/reorder). Do NOT split the run-on into chips.
- All strips are mechanical per base-spec §3.4.

### 3.4 Trust boundary (amends base §3.1)
Publishable prose = `created_by='crawler'` AND
`type IN ('education','teaching','profile','research_statement','award','service')`.
`type='about'` (LLM bios) remains HARD-EXCLUDED. `research_areas` KB items are read via
`research_of_person` (they are structured area data, not free prose).

## 4. Testing (TDD, grounded on live rows)
- Award de-noise: Sharma's 9 rows → 6 awards, zero bare-year rows; a clean-award person (Koutis) →
  unchanged. Bare-year regex unit test.
- Prefix strip: statement/service lead-in removed; interior text byte-identical (verbatim guard).
- Research fallback: a stubbed faculty dict with `areas=[]` + statement → renders statement, NO hook;
  `areas=[]` + no statement → hook (honest-empty preserved, G5).
- Trust boundary: assert no `type='about'` or non-crawler prose ever emitted (extend the existing test).
- Coverage/idempotency: `build_all` byte-identical on rerun; the 15 no-data faculty still hook.

## 5. Goals shipped/deferred (to be filled at PR)
- G1 __ · G2 __ · G3 __ · G4 __ · G5 __

## 6. Open questions
- **Q1 (needs owner call — reviewers split): show `research_statement` for all 28, or fallback-only?**
  - *Data-fidelity: show all 28* — verbatim crawler prose; the "NJIT content served VERBATIM, never
    withheld" hard line makes fallback-only a soft-withhold; and a few statements carry extra sections
    (Patents / In Progress) beyond the bare interest list that fallback-only would hide.
  - *Senior-eng: fallback-only* — for the ~26 who have both, the statement is literally the same
    `Research Interests <list>` that already feeds the chips → visible duplication.
  - *My recommendation: show all 28*, honoring the never-withhold hard line; render it as a distinct
    labelled block below the chips so the duplication reads as "source prose," not a bug. (Sharma/fh224
    have empty chips, so for them it's the only research content either way.)
- Q2: Service run-on rendered verbatim as one block (draft) — accepted by reviewers. No split.
- Q3: Awards rendered fully verbatim from the `title` column (mechanical; no risk). Accepted.
- Q4 (NIT): stripped statements still begin with the structural label `Research Interests `. Leave
  verbatim (draft) or strip that one known label too? Draft = leave (safest under verbatim).

## 7. Review folded (senior-eng + data-fidelity, 2026-07-06)
- **BLOCKER (scholar leak) — RESOLVED:** dropped the `research_of_person` reuse; area path is now
  crawler-only inside `db.py` (§3.1). No `created_by='scholar'` area ever published.
- **BLOCKER (dept-mandatory prefix regex) — RESOLVED:** strip-to-first-colon, dept-optional (§3.3);
  awards render from the clean `title` column so they need no strip at all.
- **Casing/dedup regression (Oria garbled chips) — RESOLVED:** dedup via db.py's punctuation-stripping
  `_area_key`; scholar rows (the source of the truncated `"Recommender Systems..."`) excluded.
- **Blast radius — MINIMIZED:** area path barely changes (adds meh43's 1 crawler area); existing chips
  keep crawler casing. Rebuild diff is now ~the 28+6+4 affected pages, not all 57.
- **Byte-stability:** statement/service reads take `ORDER BY id LIMIT 1` (0 faculty have >1 today, but
  durable). Award order = stable sort on desc leading year.
- **Verified data-safe:** bare-year de-noise loses no real award; 15 CS faculty stay honest-empty;
  the 3 new types are 100% `created_by='crawler'` (0 dashboard/LLM rows); no cross-person bleed.

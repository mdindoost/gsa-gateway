# FacultyFolio — Static-Site Generator Design

**Date:** 2026-07-03
**Author:** Mohammad Dindoost (owner) + Claude (design), Fable (design review)
**Status:** Design — awaiting senior-eng review + owner sign-off before build
**Scope of first build:** ~57 NJIT Computer Science faculty → profile pages + CS leaderboard

---

## 1. Purpose

Generate a public static site — one HTML page per NJIT faculty member plus per-department
leaderboards — directly from the existing GSA knowledge graph. The design is **fixed** by two
hand-built reference pages (`docs/samplepages/koutis.html`, `docs/samplepages/kieran.html`);
this generator **generalizes those into a data-driven generator, it does not redesign them**.

The product's entire value proposition is **trustworthy, honest, non-embarrassing, professional**.
Every design decision below serves that.

**Read-only against the DB.** The generator never writes the KG, crawlers, or Kavosh. It reads
`gsa_gateway.db` and emits static files into a separate output tree that becomes the public
`Faculty-Folio` GitHub Pages repo (output only — no DB, no logic, no secrets in that tree).

---

## 2. Architecture — swappable layers

Built so any layer can be replaced without touching the others.

```
facultyfolio/
├── db.py         Data layer:  faculty node (by id/slug) → clean faculty dict. Pure reads. No HTML.
├── rank.py       Ranking:     per-department citation rankings + coverage denominator (N of M).
├── format.py     Mechanical formatters: venue, research areas, teaching, education, office, numbers.
├── chart.py      Pure function: cites_per_year dict → inline SVG string (bar chart geometry).
├── photos.py     Asset layer: download Scholar photo → assets/photos/<slug>.jpg; silhouette detect; monogram fallback.
├── assets.py     Asset layer: copy style.css + self-hosted fonts into the output tree.
├── render.py     Pure function: faculty dict → HTML string, via Jinja2 templates. NO DB calls.
├── build.py      Orchestrator: for each faculty → dict → render → write; then leaderboards; then assets. Idempotent.
├── config.py     Output paths, sync-date source, suppression list, CS org id.
└── templates/
    ├── base.html         Shared shell + tokens link (nav, provenance rail, footer, grid).
    ├── profile.html      extends base — the faculty page.
    └── leaderboard.html  extends base — the department leaderboard.
```

**Data flow:** `build.py` → (`db.get_faculty(slug)` + `rank.compute(dept)`) → faculty dict →
`render.profile(dict)` → HTML string → write to `p/<slug>.html`. `render.py` is a **pure
function of the dict** (trivially testable, no I/O). `db.py` is the only module that touches SQLite.

**Layer boundaries (what/how/depends-on):**
- `db.py` — *what:* returns a validated faculty dict. *depends on:* sqlite3, schema. *swap:* a different KG backend.
- `rank.py` — *what:* `{rank_of(slug), coverage(dept)=(N,M), ranked_list(dept)}`. *depends on:* db. *swap:* a different ranking metric.
- `render.py` — *what:* dict → HTML. *depends on:* Jinja2, format, chart. *swap:* a different template set / design.
- `format.py`, `chart.py` — *what:* pure string transforms. *depends on:* nothing. *swap:* different rules.

---

## 3. Trust boundary (the foundation — non-negotiable)

1. **Only crawled/structured facts are published.** Publishable prose is restricted to
   `knowledge_items` with `created_by='crawler'` and `type IN ('education','teaching','profile')`.
   (Verified: 314 `type='about'` rows exist — **LLM-generated bios — and must NEVER be emitted.**
   The filter is `created_by='crawler'`, which also excludes the lone `profile/dashboard` row.)
2. **Never render LLM-generated prose** — no bios, no summaries, no data-driven editorial phrasing.
   Section headings are **fixed for everyone** (no per-person "An ascending trajectory" — that is a
   value judgment computed from data and crosses the boundary).
3. **The About block is labelled** `Crawled from the NJIT department profile · not written or generated`.
4. **Mechanical vs. editorial line** (governs all string formatting): *a transform is mechanical
   (allowed) iff it can execute on a string it has never seen, with no maintained lookup table, and
   produce a defensible result.* If it needs a human to have pre-decided "this input → this output"
   (a venue abbreviation dictionary; which courses to drop), it is editorial curation — **forbidden.**
   → We ship **strict-mechanical formatting (Option A)**; we **never** build curation dictionaries.
5. **Venue mojibake** (the `�` replacement char in some Scholar strings) is stripped before render.

---

## 4. Page model — uniform skeleton, adaptive rows (Fable's hybrid; owner-approved)

**Every faculty page has the same five sections, in the same order, always:**
Research → About → Scholarly activity → Publications → Recognition.

- **Sections are claims about a person** → their absence is conspicuous, so they **always render**.
  A missing *data source* gets a dignified claim-hook empty-state (same `.hook` component as
  Recognition), never a vanished section.
- **Rows/elements are individual facts** → a missing fact is invisible, so **omit it silently**
  (no `Office: —` dead rows).
- **Empty-state budget:** an empty-state must carry the claim hook / real disambiguation; ≤ 2 per page.

### Per-section / per-element rules

| Element | Rule |
|---|---|
| **Photo** | Download the Scholar photo → `assets/photos/<slug>.jpg`, reference locally (never hotlink googleusercontent — it rotates/404s). If the Scholar photo is Google's grey silhouette default (detect by URL `avatar_scholar_128.png` **and** byte-hash of the known default) or download fails → render a **first-party initials monogram** (SVG, site type system) on the `--hair` circle. Never ship the grey silhouette. |
| **Name / title** | Name from node; title from the **home** role edge (`category='faculty'`) `attrs.titles`, joined with `, ` (e.g. "Professor, Department Chair"). |
| **Department block** | Home dept = home role's Org name (plain, e.g. "Computer Science"); if a `category='joint'` role exists, add a line "Joint appointment · <Org>"; then "<College> · NJIT" from the org tree (college = parent Org, e.g. "Ying Wu College of Computing"). Dept name links to that dept's leaderboard (`../cs/index.html`). |
| **Social icons** | Render only the links that exist: email (always, `mailto:`), then website, Google Scholar, GitHub, ORCID, LinkedIn — each iff present in `attrs.profiles` (website also matches Scholar `homepage`). Fixed icon set + order; omit absent ones. |
| **Research areas** | Section always renders. Tags from `researches` edges, **verbatim** (mojibake-clean + trim only — do NOT shorten "Algebraic algorithms for computationally hard problems"). First tag dot is red (`--nav-active`), rest blue (CSS `:first-child`). If zero areas → hook empty-state: "Research areas aren't listed on public profiles yet — claim this page to add them." |
| **About → Appointment row** | Always (title + dept + college assembled from structured fields). |
| **About → Education row** | Render **only if** the parsed education has degree **+ institution** (min). A lone "Ph.D." with no institution/year → **omit the row** (adds nothing beyond what the title implies; looks like a parse failure). Never pad. |
| **About → Office/contact row** | Render iff `office` **or** `phone` present. Value = office (cleaned, e.g. "4105 GITC") · email · phone (each bit only if present). If neither office nor phone → omit row (email still in social icon). |
| **About → Teaching row** | Render iff teaching courses exist (see §7 formatter). |
| **About source label** | Always present under the block. |
| **Metrics (3 stats)** | If Scholar exists: Citations / h-index / i10-index at full size regardless of magnitude (482 renders identically to 2,791 — the fairness the uniform skeleton buys). Each shows "N since <recent_since_year>". Numbers formatted with thousands commas. |
| **4th stat** | **Always "Publishing since <first cites_per_year>· N years active".** Department rank is **never** shown on a personal page (rank is zero-sum; a blank rank slot is decodable as "bottom half"). Rank lives only on the leaderboard. |
| **Citations-per-year chart** | Render iff Scholar exists **and** ≥ 4 years of `cites_per_year` data. Below 4 years → omit the chart (the metrics card just ends after the stat row; section still has content). See §6 for geometry. |
| **Missing Scholar entirely** | The Scholarly-activity section still renders, collapsed to **one** hook box: "No Google Scholar profile is linked for <name> yet — is this you? Claim this page to connect it." (Metrics + chart + publications fold into this single box — strongest claim-funnel trigger.) |
| **Publications** | Heading always "Selected work" (true for everyone — even Koutis's list is a selection; never "full list"). Two lists: Most-cited (`top_cited`) / Most-recent (`newest`), JS toggle. Each row: citation count ("—" if 0) · title (linked to `citation_for_view` URL) · formatted venue. Sub-line "Live from Google Scholar · sortable" (drop the sample's hand-added editorial flavour like "spanning physics & ML"). If Scholar but no pubs (rare) → fold into the metrics box. |
| **Recognition** | Fixed claim-hook, identical for all (the pattern that proves the empty-state model). |

---

## 5. Design-system replication

- **Tokens:** the `:root` custom-property block from the reference (colors, fonts, radius, shadow)
  moves **verbatim** to the top of `assets/style.css`. Changing the look = editing variables only.
- **One shared stylesheet:** the reference's inline `<style>` is extracted **byte-for-byte** into
  `assets/style.css`, linked from every page (`../assets/style.css` — profiles live in `/p/`,
  leaderboards in `/cs/`, both one level deep). Include kieran's extra `.edu` rule for completeness.
- **Shell (`base.html`):** dark sticky nav (Home / Publications / Teaching + `FacultyFolio` wordmark,
  red active-tab underline); provenance rail ("Crawled from public sources · Synced <date> ·
  Scholar + NJIT-<DEPT> · Is this you? Claim this page →"); the 300px + 1fr grid; footer. Identical
  to the reference.
- **Sync date:** from Scholar `updated_at` (e.g. `2026-06-30` → "Synced 30 Jun 2026"). Per page.
- **Fonts — self-hosted** (spec requirement; the reference hotlinks Google Fonts, we do NOT across
  ~57+ pages): vendor woff2 for the exact weights used — Fraunces 500/600, Inter 400/500/600,
  IBM Plex Mono 400/500 — into `assets/fonts/`, with `@font-face` (`font-display:swap`) at the top
  of `style.css`. One-time build-time fetch; committed to the output repo.
- **Toggle JS:** the reference's publications toggle script, verbatim, in `base.html`.

---

## 6. Citations-per-year chart (`chart.py`, pure)

Inline SVG, `viewBox="0 0 660 134"`, baseline `y=116`, max bar height `108` (peak bar reaches
`y=8`). Deterministic geometry from `cites_per_year`:

- `peak = max value among NON-partial (full) years`. `scale = 108 / peak`. `height_i = value_i·scale`.
- **Partial year = the latest year** (mid-year snapshot); rendered `class="bar partial"` (dimmed,
  opacity .55) and **excluded from the peak** so a partial year never reads as a decline or a new peak.
- Bars: `N` years across width 660. `step = (660 - gap·(N-1)) / N` is derived so bars fill the width;
  gap chosen to visually match the samples (≈ constant small gap). `rx=1.5`. `bar peak` on the peak
  year (accent fill), `bar` otherwise (accent-soft).
- Axis labels: first year (start-anchored), peak year (centered under peak bar), last/partial year
  (end-anchored). Peak value label above the peak bar. `<title>` tooltip on each bar ("2010: 62",
  partial suffixed "(partial)").
- `role="img"` + `aria-label` summarizing range + peak.

*Geometry is recomputed from real data — it will be sub-pixel-different from the hand-built sample
SVGs but structurally identical (same peak scaling, partial dimming, label placement). The DATA is
real, which is the point.*

---

## 7. Mechanical formatters (`format.py`, Option A strict — pure)

All input-agnostic; no maintained lookup tables.

- **Venue** (`top_cited`/`newest` rows): (1) strip mojibake/HTML entities; (2) if a parenthetical
  acronym is present, keep it (`…(FOCS)…` → `FOCS`); (3) pattern-strip ordinal/organizer noise
  (`\d+(st|nd|rd|th)\s+Annual`, `IEEE Symposium on`, `Proceedings of the`, `arXiv preprint`, …) — by
  regex, never a venue list; (4) collapse duplicate year tokens; append the single year → `FOCS 2010`.
  If no acronym → longest clean title segment before the first comma + year. `arXiv:…` → "arXiv <year>".
- **Research areas:** mojibake-clean + trim; render **verbatim** (no shortening — that's editorial).
- **Teaching:** (1) split the run-on on `((CS|DS|IT|…)\s?\d{3}):` boundaries (structural regex);
  (2) title-case the SHOUTING (`INTRO TO MACHINE LEARNING-HONORS` → "Intro To Machine
  Learning-Honors") — pure casing; (3) keep course code + title (do NOT hand-rewrite abbreviations);
  (4) **dedupe by normalized course number** — `CS 610` appears once; cross-listed `CS 675 / DS 675`
  collapse to one entry showing both codes. Dedupe on the number = mechanical; dropping a course for
  looking minor = forbidden. Strip a leading "Past Courses;" marker.
- **Education:** parse the `;`-delimited crawler string into records
  (`degree; institution; field; year`). Render "Degree Field, Institution (Year)" joined by " · ".
  A record missing institution → the whole row is omitted per §4 (degree-only is not enough).
- **Office:** trim the long form to a short campus form (e.g. strip "Guttenberg Information
  Technologies Center" → keep "GITC" via the same parenthetical-acronym rule); mechanical only.
- **Numbers:** thousands separators.

---

## 8. Ranking & coverage (`rank.py`)

- **Department membership (M):** persons with an active `has_role category='faculty'` edge to the
  department Org (CS = node id 16). Joint appointments count toward their **home** dept only.
  (Verified: CS M = 57.)
- **Ranked set (N):** members with an integer `attrs.profiles.scholar.citations`. (Verified: N = 39.)
- **Ranking:** by total citations, descending. One source of truth used by BOTH the leaderboard and
  (internally) any rank logic — though per §4 rank is **not** shown on personal pages.
- **Coverage denominator:** `(N, M)` → the leaderboard header renders "Ranked among N of M faculty
  with Google Scholar data" (honesty is a designed element, not fine print).

---

## 9. Leaderboard page (`leaderboard.html`)

No hand-built reference exists → designed **within the established system** (same tokens, nav, rail,
footer, type). **Will be shown to the owner before fanning out.**

- Header: department name + prominent coverage line "Ranked among 39 of 57 faculty with Google
  Scholar data" + axis label "by total citations" (explicitly one lens, not the definitive ranking).
- Body: ranked rows (rank · name · total citations · h-index), each linking to `../p/<slug>.html`.
- Uses the shared shell; provenance rail scoped to the department.

---

## 10. Visibility / suppression

Every faculty node has a visibility concept — **default publish**. A `suppressed` marker means
never emit a page even on regenerate. Since the DB is read-only, suppression is a **config list**
(`config.py` / a `suppressed.txt` of slugs) checked in `build.py`. Claim/opt-out isn't built yet;
this wires the hook now. Suppressed faculty are also excluded from leaderboard rows.

---

## 11. Output layout & idempotency

```
Faculty-Folio/
├── p/<slug>.html          one per publishable CS faculty (slug = node key tail, e.g. ikoutis)
├── cs/index.html          CS leaderboard
└── assets/
    ├── style.css          one shared stylesheet (tokens at top)
    ├── photos/<slug>.jpg   downloaded headshots (monogram fallback rendered inline, not a file)
    └── fonts/…             self-hosted woff2
```

- **Idempotent:** re-running `build.py` regenerates every page + leaderboard. Photos are cached
  (skip re-download if a good file exists) but output is deterministic.
- **Slug** = tail of node key `people.njit.edu/profile/<slug>`.

---

## 12. Testing strategy (TDD)

- **`db.py`** — golden test against **Koutis (node 33)**: assert the faculty dict (name, title
  "Associate Professor", office, email, 4 profile links [scholar/linkedin/github/website], 5 research
  areas, education 2 records, teaching, full scholar bag).
- **`format.py`** — unit tests per formatter: venue (`FOCS 2010` from the raw string), teaching
  (dedupe cross-listed `CS 675 / DS 675`), education (degree+institution → row; degree-only → omit),
  office (short form).
- **`chart.py`** — peak excludes partial year; heights scale to peak; ≥4-year gate.
- **`render.py`** — pure-function golden: Koutis dict → HTML contains the right sections; **degradation
  cases** as fixtures: junior (Kieran — no office row, Publishing-since stat), degraded education
  (Oria — no education row), grey-silhouette photo → monogram, missing-Scholar → single hook box,
  zero research areas → hook.
- **Trust-boundary test:** assert no `type='about'` content and no `created_by!='crawler'` prose ever
  reaches the dict/HTML.
- **Build first against Koutis, confirm against the reference design, then fan out to all CS.**

---

## 13. Goals checklist (shipped / deferred)

**Shipped in this build:**
- [ ] db/rank/format/chart/render/build layered generator; templates in Jinja2
- [ ] All ~57 CS profile pages + CS leaderboard, matching the reference design system
- [ ] Uniform-skeleton + row-level-adaptive page model (all per-section rules in §4)
- [ ] Strict-mechanical formatting (§7); verbatim research areas; venue mojibake cleaned
- [ ] Photo download + grey-silhouette detection + monogram fallback
- [ ] Self-hosted fonts; one shared tokenised stylesheet
- [ ] Coverage denominator on leaderboard; rank cut from personal pages
- [ ] Suppression hook; trust-boundary filter (crawler-only prose, no `about`)
- [ ] Idempotent build into the separate `Faculty-Folio` output tree

**Explicitly deferred (flagged, not silently dropped):**
- Claim / opt-out flow (only the suppression hook is wired now)
- Other departments + the whole university (this build is CS only; adding a dept = config + org id)
- Awards/recognition data (not crawlable — permanent claim-hook for now)
- Any curation dictionaries (rejected by design, not deferred)

---

## 14. Open decisions folded (defaults chosen, owner may override)

- **Phone placement:** appended to the About Office/contact row when present (row renders if office
  OR phone). — *Reference pages had no phone data to show; this is the chosen placement.*
- **Fonts:** fetched at build time (one-time vendor) and committed to the output repo.
- **Suppression mechanism:** config-file list (DB is read-only).
- **"Department of" prefix:** dropped — use the plain Org name ("Computer Science"), matching kieran
  and the KG (koutis sample's "Department of" was a hand liberty).

---

## 15. Non-goals

- No DB writes, no KG/crawler/Kavosh changes.
- No dynamic server; pure static output.
- No LLM anywhere in the generator.

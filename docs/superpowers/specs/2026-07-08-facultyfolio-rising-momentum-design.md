# FacultyFolio — "Rising" citation-momentum view (design)

> Status: DESIGN (awaiting owner review). Reviewers: Fable (design/RAG-research) — signed off on
> the approach and the honesty guards (two review rounds, real-data validation). Senior-eng review
> pending before build. Build only after owner approval (EXPERT-REVIEW HARD GATE).

**Goal:** Add a fourth faculty-directory view — **★ Rising** — that surfaces the faculty whose
Google-Scholar citations are *currently growing fastest*, so an early-career researcher on a steep
climb is visible next to (not buried under) the lifetime-citation leaders. One selectable tab
alongside the existing Rank / Citations / A–Z, on every department page, generalizable to all colleges.

**Architecture:** Pure **build-time** computation in the static-site generator (Python, reading the
KG's `attrs.profiles.scholar.cites_per_year`), baked into the page as HTML + data. No server, no LLM,
no live DB at request time — GitHub Pages serves the static files and client-side JS only re-sorts /
filters what is already baked in. The anti-fabrication surface is therefore just the label/caption text.

**Tech stack:** Python 3.11, existing `facultyfolio/` package (db.py → rank.py → format.py/chart.py →
render.py → build.py), Jinja2 templates, vanilla JS (matching the current leaderboard's tab switch).

---

## Global Constraints (verbatim, apply to every task)

- **Nothing hardcoded; generalizable to every department and college** from the DB. The view is
  computed per-org via `db.faculty_slugs(org_id)`; adding a college/dept requires no code change.
- **Anti-fabrication / honest-partial (HARD LINE).** Never assert a trend the data can't support.
  No "declining" label, no red arrow, no bottom-of-the-board framing anywhere.
- **`%/yr` is NEVER rendered without, in the same row, the sparkline AND an absolute recent-rate chip.**
  This is the single rule whose violation would embarrass us (Fable).
- **Citations are counts, not a quality judgment** — a one-line DORA/Leiden-flavored footnote on the view.
- Within-department only. No cross-department or cross-college momentum comparison.
- Deterministic + mechanical. No LLM anywhere near this feature.

---

## Data & definitions

**Source:** `nodes.attrs.profiles.scholar.cites_per_year` — a `{year: citations_received_that_year}`
map already in the KG for ~220 people (202 have ≥8 years). Read via the existing `db.get_faculty`.

**Window:** the **trailing 5 complete years**, *excluding the current (partial) sync year*. "Complete"
= not the current calendar/sync year (citations for the current year are still accruing and always dip;
they must be excluded from the math, not merely dimmed). Concretely with sync year 2026 → window
2021–2025.

**Momentum estimator (mechanical, citable):**
- Compute the **Theil–Sen slope** (median of all pairwise slopes) of `log1p(cites)` over the window's
  points (x = 0..k-1). `log1p` compresses small-base blow-ups so a 4→8 series can't dwarf a 200→400 one.
- Report as a percent-per-year: `momentum_pct = (exp(theil_sen_slope) - 1) * 100`, rounded to a whole %.
- Theil–Sen is robust to a single spiky year (one viral survey paper) and needs no fitting library
  (~10 lines). It is the answer we give a faculty member who emails "how was this computed?".

**Absolute recent rate:** the latest complete year's citations (e.g. "recent: 139 cites/yr") — shown
beside every momentum figure.

---

## Eligibility (two gates, both mechanical)

1. **Data gate (can we compute a trend at all?):** `≥5 complete years` in the window AND a **magnitude
   floor** of `mean(window) ≥ 10 cites/yr`. Below the gate the person is *not eligible* for the Rising
   view — they are not shown here (they remain on Rank/Citations/A–Z). This kills 3→8 numerology.
2. **Rising membership (zero-threshold inclusion rule — the key design decision):** among data-gate
   passers, **include a person iff their trend is positive even on the pessimistic read** — i.e.
   `theil_sen_slope > 0` AND the **lower bound of the pairwise-slope spread > 0** (the 25th-percentile
   pairwise slope, say, is still ≥ 0). Zero is the only non-arbitrary cutpoint: "measurably growing vs.
   not". No top-N, no quartile, no k-means seam — the count falls out of the data (~20 of 39 for CS).

**Why no rendered "last place" (Fable, decisive):** last position on a board named *Rising* is not a
neutral coordinate — it reads as "least rising = declining", which is an *assertion* under a named
person's photo, resting on a lag-contaminated signal. So the bottom must not exist as a rendered
position. The Rising view is therefore a **strip of qualifying risers**, not a full 1→N ladder of
everyone. Faculty with flat/negative momentum simply aren't featured on this one lens.

**Why this is fair, not a cover-up (omission ≠ erasure):** the genuinely-flat/declining seniors are
already at the **top** of the default **Citations** and **Rank** views (their large lifetime totals).
Not appearing on one optional momentum tab, while dominating the primary tab, cannot be read as "no
impact." Enforced by: **Citations/Rank stays the default view; Rising is a non-default tab.**

---

## Rendering (the row + the framing)

**Sort:** eligible risers sorted by `momentum_pct` descending (relative momentum is the whole point;
sorting by absolute rate would just reproduce the seniority ladder and make the view redundant).

**Each Rising row shows, non-optionally:**
- photo / monogram, name, title (as today);
- the **sparkline** of the window (reader instantly sees base size + shape — tiny-then-spike vs
  tall-and-climbing);
- the **momentum** figure *with its window*: `+109%/yr (2021–2025)`;
- an **absolute recent-rate chip**: `recent: 139 cites/yr`.

**Tiny-base guard:** if the latest complete year `< 25 cites`, render a `▲ growing` glyph instead of a
precise percentage (a precise huge % on a small base looks like a magnitude claim it isn't). For volumes
≥ 25 (e.g. Zhihao Yao's 139) the precise figure is fine.

**Caption (names the mechanism — converts omission into stated scope):**
> "Faculty whose annual citations grew over 2021–2025 (2026 excluded, still accruing). Citations lag
> research by 2–5 years, and faculty with large established citation bases naturally show flatter recent
> growth — this view highlights recent momentum, not overall impact or research quality. See the
> Citations view for lifetime totals."

**Coverage funnel (honesty anchor):** "Recent-momentum view: 20 of 39 CS faculty with growing citations
(Scholar-listed, ≥5 complete years)." The "of 39" tells the reader this is a filtered slice, not a
verdict on the department.

**Footnote:** one line — citation counts support, don't replace, judgment (DORA/Leiden Manifesto flavor).

---

## File structure

- **Create `facultyfolio/momentum.py`** — pure functions, no I/O: `theil_sen_slope(values)`,
  `momentum_pct(window_series)`, `pairwise_slope_lo(values)`, `eligible(series, n_complete)` →
  data-gate bool, `is_rising(series)` → membership bool, `rising_view(faculty_rows, sync_year)` →
  the sorted list of `{slug, name, title, series, window, momentum_pct, recent_rate, tiny_base}`
  view-model dicts. All mechanical, trivially unit-testable.
- **Modify `facultyfolio/rank.py`** — add a `rising` roster view alongside `rank`/`citations`/`az`,
  built by calling `momentum.rising_view(...)` on the org's faculty (window derived from each person's
  sync year; per-person, so mixed sync years are handled).
- **Modify `facultyfolio/render.py`** — `render_leaderboard` takes the new `rising` view; a
  `_rising_row(...)` builder (photo, sparkline via existing `chart`/sparkline helper, momentum chip,
  absolute chip, tiny-base glyph). Reuse `chart.py` for the inline sparkline (small variant).
- **Modify `facultyfolio/templates/leaderboard.html`** — add the `★ Rising` button to `.lb-switch`
  and a `data-view="rising"` panel with the caption + funnel + footnote; JS toggle already exists.
- **Modify `facultyfolio/config.py`** if needed — window length (5) and magnitude floor (10) as named
  constants (not magic numbers), plus the tiny-base threshold (25).
- **Tests:** `facultyfolio/tests/test_momentum.py` (estimator, gates, membership, tiny-base),
  extend `test_render.py` (Rising panel present; % never without sparkline+chip; declining name absent
  from Rising but present in Citations), extend `test_db.py` if a new reader is added.

**Generalizability check (must hold):** running the build produces a Rising strip on CS, Data Science,
and Informatics with no per-dept code; a future college added to the entry points gets it automatically.

---

## Testing strategy

- **Estimator:** Theil–Sen slope on a known linear series returns the exact slope; robust to one outlier.
- **Momentum:** `log1p` path — a 4→139 series and a 200→400 series both produce sane %/yr; small base
  does not produce an absurd rank once the absolute chip is present (assert both are rendered).
- **Gates:** <5 complete years → ineligible; mean <10 → ineligible; exactly 5 & mean≥10 → eligible.
- **Membership:** slope>0 & pessimistic>0 → included; flat (slope≈0) → excluded; negative → excluded.
  A genuinely-declining fixture (Jason-Wang-like 442→275) is EXCLUDED from Rising and PRESENT in Citations.
- **Tiny-base guard:** latest complete year <25 → `▲ growing` glyph, no precise %.
- **Render invariant (the hard rule):** every Rising row that shows a `%/yr` also contains a sparkline
  and an absolute-rate chip — assert all three co-occur; fail the build if a % appears alone.
- **Real-data smoke (manual, gated):** rebuild against the live DB; eyeball CS Rising (~20 names,
  Zhihao Yao #1 with his 139 chip visible), confirm Jason Wang / Ali Mili are absent from Rising and
  top the Citations view.

---

## Goals checklist (shipped / deferred — fill at PR)

**In scope (this spec):**
- [ ] ★ Rising tab on every department page, generalizable to all colleges (no hardcoding).
- [ ] Theil–Sen-on-log1p momentum, 5 complete years, partial year excluded from math.
- [ ] Data gate (≥5 yrs, mean ≥10/yr) + zero-threshold Rising membership (positive even pessimistically).
- [ ] Strip of risers, sorted by relative %/yr; NO rendered last place / no ladder of everyone.
- [ ] Mandatory co-display: sparkline + absolute recent-rate chip beside every % (build-failing test).
- [ ] Tiny-base `▲ growing` guard (<25 latest-year cites).
- [ ] Honest caption (mechanism named) + coverage funnel + DORA footnote.
- [ ] Decliners kept present & top-ranked on the default Citations/Rank views (omission ≠ erasure).
- [ ] Per-person momentum line under the bar chart on each profile page — SAME rules (positive only;
      %/yr never without the chart + absolute chip beside it; tiny-base glyph). *(Confirm scope w/ owner
      — could be a fast-follow if it complicates review.)*

**Explicitly DEFERRED (loudly, per review-against-plan hard line):**
- Shape families (early-climber / plateau / past-peak) — **deferred**; if ever revisited, only as
  neutral verbatim facts ("peak citation year: 2019", "first cited: 2011"), never archetypes/diagnoses.
- Department vitality (aggregate dept curve) — **dropped** (survivorship + universal-shape + superstar-
  dominance make it misleading; Fable).
- Interactivity extras — **deferred to a later batch across all pages:** sparkline hover-tooltips and the
  3-vs-5-year window slider. (Live search already exists in the leaderboard and is inherited for free.)

---

## Prior art (informs, does not block)

Rising-star bibliometrics is an established topic (IIRL / pairwise-citation-increment ranking; Scientometrics).
Lesson taken: pure citation-increment is crude → use a smoothed multi-year window (done). Google Scholar
shows this same bar chart and makes *no* trend claim (our restraint mirrors it). AMiner "Rising Star" is the
nearest product and is criticized for verdict-y framing (we avoid it: a scope-captioned strip, not a verdict).
CSRankings refuses citations for gameability → we keep momentum a *view*, never the default sort. DORA / Leiden
Manifesto → the footnote.

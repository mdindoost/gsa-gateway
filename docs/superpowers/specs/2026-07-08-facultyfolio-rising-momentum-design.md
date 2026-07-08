# FacultyFolio — "Rising" citation-momentum view (design)

> Status: DESIGN — **CLEAR TO BUILD**. Reviewers: Fable (design/RAG/honesty) — "clear to build" after
> three rounds + real-data validation; senior-eng review — folded (B1/B2 blockers + S1–S5 + nits all
> resolved below). Build after this revision; the owner has delegated remaining sign-off to Fable for
> the session (EXPERT-REVIEW HARD GATE satisfied via the two reviews).

**Goal:** Add a fourth faculty-directory view — **★ Rising** — surfacing the faculty whose Google-Scholar
citations are *currently growing fastest*, so an early-career researcher on a steep climb is visible next
to (not buried under) the lifetime-citation leaders. One selectable tab beside Rank / Citations / A–Z,
on every department page, generalizable to all colleges. Plus a per-person "recent trend" line on each
profile page.

**Architecture:** Pure **build-time** computation in the static-site generator (Python, reading the KG's
`attrs.profiles.scholar.cites_per_year`), baked into the page as HTML. No server / no LLM / no live DB at
request time — GitHub Pages serves static files; client-side JS only re-sorts/filters/searches what is
already baked in. The anti-fabrication surface is therefore just label/caption text.

**Tech stack:** Python 3.11, existing `facultyfolio/` package (db → rank → format/chart → render → build),
Jinja2, vanilla JS (the leaderboard's existing tab-switch + `.lb-search`).

---

## Global Constraints (verbatim, apply to every task)

- **Nothing hardcoded; generalizable to every department and college** from the DB, per-org via
  `db.faculty_slugs(org_id)`. Adding a dept/college requires no code change.
- **Anti-fabrication / honest-partial (HARD LINE).** Never assert a trend the data can't support. No
  "declining"/"falling" label, no red arrow, no bottom-of-board framing, no bare negative headline %.
- **`%/yr` is NEVER rendered without, in the same row/line, the sparkline AND an absolute recent-rate chip.**
  The single rule whose violation would embarrass us — enforced by a build-failing test.
- **Citations are counts, not a quality judgment** — a one-line DORA/Leiden footnote on the view.
- Within-department only. No cross-dept/cross-college momentum comparison.
- Deterministic + mechanical. No LLM anywhere near this feature.

---

## Empirical grounding (verified against the live DB, 2026-07-08)

Across all **220** people with `cites_per_year`: **0 have internal year-gaps, 0 have a 0-value year**
(senior-eng review). Scholar emits consecutive positive years that simply stop when a record goes cold.
So within the recent window a qualifying person has all years present and positive — `log1p(0)` never
fires and `x = index` equals `x = year-offset`. The estimator is safe on real data. (We still specify the
zero/gap behavior below so a future re-crawl or provider can't silently distort a slope.)

Real results with the pinned rules below: **CS 13 risers / 35 data-gate-passers / 39 Scholar-listed;
Data Science 8 / 14 / 15; Informatics 12 / 18 / 21.** Top riser CS = Zhihao Yao `[4,14,30,77,139]` +129%/yr.

---

## Data & definitions (pinned — resolves S1)

**Source:** `nodes.attrs.profiles.scholar.cites_per_year` (`{year: citations_received}`) + per-person
`scholar.updated_at`, read via `db.get_faculty`.

**Sync year (per person):** `int(updated_at[:4])` — the year Scholar was last pulled. The current/partial
year for that person = their sync year (its citations are still accruing).

**Window (per person, fixed length 5):** the five consecutive calendar years `[sync-5 … sync-1]` — i.e.
the 5 most recent **complete** years, excluding the partial sync year. Almost all people sync in 2026 →
window 2021–2025; a person synced in 2025 → 2020–2024 (handled per-person, no global window).

**Data gate:** a person is eligible iff **all 5 window years are present** in `cites_per_year`
(equivalently ≥5, since the window is exactly 5) AND `median(window_values) ≥ 10` (magnitude floor; median
per senior-eng N2 — robust to one spike). Missing window years are **NOT zero-filled** — a person with a
hole in the window fails the gate (today: never happens; guard for the future). No manufactured pre-career
zeros.

**Momentum estimator (deterministic, citable):**
- `lv = [log1p(v) for v in window_values]` (log1p compresses small-base blow-ups; safe since v ≥ 0).
- `P = sorted[(lv[j]-lv[i])/(j-i) for i<j]` — the 10 pairwise slopes.
- `theil_sen = median(P)`.
- `momentum_pct = round((exp(theil_sen) - 1) * 100)`.
  **N1 footnote (exact formula, for the "how was this computed?" email):** this is the median per-year
  growth rate of `log1p(citations)`; because it is computed on `1+cites`, it is the growth of `1+cites`,
  a negligible approximation at the ≥25-cite volumes where a precise % renders.

**Absolute recent rate:** the latest complete window year's citations (e.g. `139`), shown as `recent: N/yr`.

---

## Membership: who appears on the Rising strip (pinned — resolves B2)

Among data-gate passers, **include iff the trend is up even on the pessimistic read:**

> `theil_sen > 0` **AND** `p25 ≥ 0`, where `p25` = the **nearest-rank 25th percentile** of `P`
> (nearest-rank: `k = ceil(0.25 · len(P))`, take `P_sorted[k-1]`; for `len(P)=10`, `k=3` → the 3rd-smallest
> pairwise slope). Meaning: at most ~25% of year-pair comparisons decline — near-monotone growth, tolerant
> of ONE noisy dip, rejecting spiky/flat/one-jump series.

`>` for the median (a flat median is not "rising"), `≥` for `p25` (allow a single zero-slope pair). This
is the exact, testable formula; `test_momentum.py` mirrors the `>` vs `≥` decision. **Zero is the only
non-arbitrary cutpoint** — no top-N / quartile-of-people / k-means seam; the count falls out of the data
(CS 13, DS 8, Informatics 12).

**Why no rendered "last place" (Fable, decisive):** last position on a board named *Rising* reads as
"least rising = declining" — an assertion under a named person's photo on a lag-contaminated signal. So the
Rising view is a **strip of qualifying risers**, not a 1→N ladder of everyone. Non-qualifiers aren't
featured on this one lens.

**Why fair, not a cover-up (omission ≠ erasure):** the flat/declining seniors are already at the **top** of
the default **Citations**/**Rank** views (large lifetime totals). Not appearing on one optional momentum
tab, while dominating the primary tab, cannot read as "no impact." Enforced structurally: **Citations/Rank
stays the default; Rising is a non-default tab.**

---

## Rendering — the strip (resolves S3, S5, N4)

**Sort:** eligible risers by `momentum_pct` descending (relative momentum is the whole point; an absolute
sort would just reproduce the seniority ladder).

**Each Rising row shows, non-optionally (the hard rule):**
- photo / monogram, name, title (as today);
- the **sparkline** of the window (see chart.sparkline, B1) — base size + shape instantly legible;
- the **momentum chip with its own window**: `+129%/yr (2021–2025)` — the window is per-row (handles mixed
  sync years; there is NO single panel-wide year range);
- an **absolute recent-rate chip**: `recent: 139/yr`.
- `data-name` / `data-title` / `data-areas` attributes on the row so the existing `.lb-search` filter works
  over the Rising panel (N4 — without these, search silently matches nothing).

**Tiny-base guard:** latest complete window year `< 25` → render `▲ growing` instead of a precise % (a huge
% on a small base looks like a magnitude claim). ≥25 → precise %.

**Round-to-zero guard (S5):** if `momentum_pct < 1` after rounding, render `▲ growing`, never `+0%/yr` on a
board named Rising. (Today the lowest real riser is +4%, but pin it.)

**Panel caption (window-free, so mixed sync years are honest; S3a):**
> "Faculty whose annual citations grew over their five most recent complete years (the current year is
> excluded — it is still accruing). Citations lag research by 2–5 years, and faculty with large established
> citation bases naturally show flatter recent growth — this highlights recent momentum, not overall impact
> or research quality. See the Citations view for lifetime totals."

**Coverage funnel — COMPUTED, not literal (S3b):** `"{n_risers} of {n_data_gate_passers} faculty with
growing citations (Scholar-listed with ≥5 complete years; {n_scholar} of {n_total} faculty are on Scholar)."`
The denominator is explicitly the data-gate passers, with the Scholar/total funnel spelled out so nothing is
ambiguous. All four counts are computed at build time per org.

**Empty-state (S4):** if `n_risers == 0` for an org (possible for a tiny/low-coverage dept), **hide the
★ Rising tab button and its panel entirely** for that page (cleaner than an empty board that reads as a
negative verdict). A test asserts the tab is absent when the strip is empty.

**Footnote:** one line — citation counts support, don't replace, judgment (DORA/Leiden).

---

## Per-person "recent trend" line (profile page) — Fable option (B)

Each **profile page** gets a small "recent trend" line under its existing cites-per-year bar chart. Shown
for **everyone who passes the data gate** (not positive-only): positive-only would migrate the "last place"
harm onto a named page, where an absent line (while peers' pages carry one) is a conspicuous negative signal
to the one reader who matters. Two-bucket neutral vocabulary that **never asserts a decline**:

- **Positive & clears the noise** (same `is_rising` rule) → `recent trend: growing (+18%/yr, 2021–2025)`
  (tiny-base or round-to-zero → `recent trend: growing ▲`).
- **Everything else with data** (flat, mildly negative, noisy) → `recent trend: steady` — no number, no
  color, no arrow.
- The words **"declining"/"falling" and any bare negative headline % NEVER render, anywhere.** Calling a
  genuine −14% "steady" is honest-partial in the correct direction — we decline to assert a decline the
  2–5-yr lag could erase; "steady" is the strongest claim the data licenses for an established record.
- **Below the data gate** (<5 window years or median <10): **no line** — data-insufficiency, symmetric with
  a sparse chart, not a judgment.
- Same lag caveat sits under it as under the strip.

Reuses the strip's `momentum_pct` / `is_rising` / tiny-base helpers — profile page just maps the result to
`{growing +X%/yr | growing ▲ | steady | None}`. No new math. The `%/yr`, when shown, sits beside the chart
already on the page (hard rule satisfied).

---

## File structure

- **Create `facultyfolio/momentum.py`** — pure, no I/O: `window_series(cites_per_year, sync_year)` →
  `(years, values)` or `None` (gate: all 5 present); `theil_sen(values)`; `pairwise_slopes(values)`;
  `p25_nearest_rank(sorted_slopes)`; `momentum_pct(values)`; `passes_data_gate(values)` (median≥10);
  `is_rising(values)` (median>0 AND p25≥0); `recent_rate(values)`; `tiny_base(values)` (<25 latest);
  `recent_trend(cites_per_year, sync_year)` → `{"kind":"growing","pct":18,"window":"2021–2025","tiny":False}`
  / `{"kind":"steady"}` / `None`; `rising_view(faculty, now_year)` → sorted list of row view-models
  `{slug,name,title,values,window,momentum_pct,recent_rate,tiny}` + the four funnel counts. Trivially unit-testable.
- **Create `chart.sparkline(values)` in `facultyfolio/chart.py` (B1)** — a NEW ~15-line minimal-geometry
  inline sparkline (small viewBox, bars only; NO axis text, NO peak label, NO year ticks — those are
  `render_chart`'s profile-chart concerns). `render_chart` is left untouched. Reused by both the strip row
  and (optionally) nothing else.
- **Modify `facultyfolio/db.py` / `facultyfolio/rank.py` (S2 plumbing)** — the roster reader currently
  returns `{slug,name,title,rank_*,citations,h_index,areas}` and omits `cites_per_year`/`updated_at`. Widen
  it (or add a `rank.rising(org_id)` that reads them) so `momentum.rising_view` has each person's full
  `cites_per_year` + `updated_at`. Add `rank.rising` producing the roster view + funnel counts.
- **Modify `facultyfolio/build.py`** — `views["rising"] = rank.rising(...)`; pass it (and funnel counts) to
  the widened `render_leaderboard` signature.
- **Modify `facultyfolio/render.py`** — `render_leaderboard` accepts the `rising` view + funnel; a
  `_rising_row(...)` builder (photo, `chart.sparkline`, momentum chip, absolute chip, tiny/zero glyph,
  `data-*` search attrs). In `render_profile` / `_scholar_ctx` (render.py:134) add a `recent_trend` field via
  `momentum.recent_trend(...)`.
- **Modify `facultyfolio/templates/leaderboard.html`** — add the `★ Rising` button to `.lb-switch` and a
  `data-view="rising"` panel (rows + computed caption + funnel + footnote), rendered only when non-empty;
  omit the button when empty (S4). JS toggle + search unchanged.
- **Modify `facultyfolio/templates/profile.html`** — render the `recent_trend` line under the chart
  (insertion point profile.html:87–90, after `{{ chart_svg|safe }}`), only when `recent_trend` is non-None.
- **Modify `facultyfolio/config.py`** — named constants: `MOMENTUM_WINDOW=5`, `MOMENTUM_FLOOR=10`,
  `MOMENTUM_TINY_BASE=25` (no magic numbers).
- **Tests:** `facultyfolio/tests/test_momentum.py` (estimator, gates, membership, tiny/zero, recent_trend);
  extend `test_render.py` (Rising panel + hard-rule test + empty-state hides tab + `declin`/`falling` absent);
  extend `test_db.py`/`test_config.py` for the widened reader + constants.

**Generalizability check (must hold):** the build produces a Rising strip on CS, Data Science, Informatics
with no per-dept code; a future college in the entry points gets it automatically.

---

## Testing strategy

- **Estimator:** `theil_sen` on a known linear log-series returns the exact slope; robust to one outlier.
- **Window/gate (S1):** all-5-present + median≥10 → eligible; a missing window year → ineligible (no
  zero-fill); median<10 → ineligible; a 0-value year in-window would fail present-check today (guard test).
- **Momentum:** `[4,14,30,77,139]` → ~+129%; `[144,…,450]` → ~+34%; both render sane; small base + big base
  both keep their absolute chip (assert co-display).
- **Membership (B2, exact):** `median(P)>0 AND p25≥0` — monotone `[4,14,30,77,139]` IN; one-dip
  `[32,23,49,68,110]` IN (Asad Raza — tolerated); flat `[96,100,98,99,100]` OUT; one-spike
  `[2,2,2,2,45]` OUT; genuine decline `[442,388,…,275]` OUT. Nearest-rank p25 pinned (3rd-smallest of 10).
- **Tiny-base / zero:** latest <25 → `▲ growing`; `momentum_pct<1` → `▲ growing`, never `+0%/yr`.
- **Per-person trend:** positive fixture → "growing (+X%/yr, window)"; flat → "steady"; genuine −14% → "steady"
  (NOT "declining"); <5 yrs → None. Assert `declin`/`falling` never in any rendered profile.
- **Strip render invariant (hard rule):** every Rising row with a `%/yr` also contains a sparkline AND an
  absolute-rate chip — assert co-occurrence; a % alone fails the build.
- **Empty-state (S4):** an org with 0 risers renders NO `data-view="rising"` button/panel.
- **Funnel (S3):** counts computed, denominator = data-gate passers; assert the rendered string matches
  computed `{risers}/{gated}` and includes the Scholar/total funnel.
- **Real-data smoke (manual, gated):** rebuild vs live DB → CS Rising = 13 names, Zhihao #1 with `recent: 139/yr`
  visible; DS 8, Informatics 12; Jason Wang / Ali Mili absent from Rising, atop Citations.

---

## Goals checklist (shipped / deferred — fill at PR)

**In scope (this spec):**
- [ ] ★ Rising tab on every department page, generalizable via `db.faculty_slugs` (no hardcoding).
- [ ] Theil–Sen-on-log1p momentum; per-person 5 complete years; partial year excluded; exact formula.
- [ ] Data gate (all-5-present, median≥10) + pinned membership (median(P)>0 AND nearest-rank p25≥0).
- [ ] Strip of risers, sorted by relative %/yr; NO rendered last place.
- [ ] Mandatory co-display: sparkline + absolute recent-rate chip beside every % (build-failing test).
- [ ] `chart.sparkline` actually created (B1); tiny-base + round-to-zero → `▲ growing` (S5).
- [ ] Computed caption (window-free) + computed coverage funnel (S3); empty-state hides the tab (S4).
- [ ] Decliners kept present & top-ranked on the default Citations/Rank views (omission ≠ erasure).
- [ ] Roster reader widened to carry `cites_per_year` + `updated_at` (S2).
- [ ] Per-person neutral "recent trend" line on every profile with ≥5yr data (Fable option B).
- [ ] DORA/Leiden footnote.

**Explicitly DEFERRED (loudly, per review-against-plan hard line):**
- Shape families (climber/plateau/past-peak) — **deferred**; if revisited, only neutral verbatim facts
  ("peak citation year: 2019"), never archetypes/diagnoses.
- Department vitality (aggregate dept curve) — **dropped** (survivorship + universal-shape + superstar
  dominance make it misleading; Fable).
- Interactivity extras — **deferred to a later cross-page batch:** sparkline hover-tooltips, 3-vs-5-year
  window slider. (Live search already exists and is inherited free.)

---

## Prior art (informs, does not block)

Rising-star bibliometrics is established (IIRL / pairwise-citation-increment ranking; Scientometrics). Lesson:
pure citation-increment is crude → smoothed multi-year window (done). Google Scholar shows this same bar
chart and makes *no* trend claim (our restraint mirrors it). AMiner "Rising Star" is the nearest product,
criticized for verdict-y framing (we avoid it: a scope-captioned strip, not a verdict). CSRankings refuses
citations for gameability → we keep momentum a *view*, never the default sort. DORA / Leiden Manifesto → the
footnote.

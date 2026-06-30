# Surfacing Scholar Capture — Routing (Pull) + Deterministic Suffix (Push)

**Date:** 2026-06-30
**Author:** Claude (brainstormed with Mohammad)
**Status:** Design — pending owner review + expert review (HARD GATE), build deferred to a later session
**Depends on:** the maximal Scholar capture (live in the KG for all 211 faculty as of 2026-06-30,
`2026-06-29-scholar-fetcher-maximal-capture-design.md`)
**Related:** [[project_faculty_page_builder]] (Folio), [[project_find_your_advisor]]

---

## 1. Goal

Make the new Scholar data (per-year citations, peak year, top/newest/this-year papers, recent
metrics, history) **answerable by the bot** (co-authors are captured but surfaced in the
fast-follow, not this build — §2) — through the **structured** path, rendered
**deterministically** (never LLM-reworded), honoring the anti-fabrication hard line. Two delivery
modes:

- **PULL** — the user asks → `router` matches → a `skill` reads the field → deterministic answer.
- **PUSH** — a compact deterministic **suffix** auto-appended to an existing answer (the way links
  append to "who is X" and metrics to "X's research" today).

## 2. Scope

**This build (3 clusters):**
1. **Recent metrics** — `recent_citations / recent_h_index / recent_i10_index` (+ activity share).
2. **Papers** — most-cited / newest / this-year, per person.
3. **Trend & peak-year** — peak year (honest guard), citations-in-year, year-over-year growth.

**Fast-follow (NOT this build), enumerated so they're not silently dropped:**
- Cross-person rankings (most-cited *paper* in an org, fastest-growing, best-year, dept "newest
  papers" feed, most-collaborative) — each scans all faculty attrs.
- Co-authors (pull).
- Since-last-refresh fine deltas (needs ≥2 snapshots; today every person has exactly 1).
- X-vs-Y comparisons; area+metric advisor combos (the Find-My-Advisor seed).

## 3. Architecture — reuse, don't invent

- **Pull skills** live with the existing entity skills in `entity.py`, reading per-person data via
  `entity.person_attrs()` (the centralized reader); routed by deterministic rules in `router.py`;
  rendered by `structured_answer.py`. **Both new skills (`papers_of_person`,
  `citation_trend_of_person`) MUST be added to `_DETERMINISTIC_SKILLS`** (`structured_answer.py:~99`)
  so `is_deterministic(result)` is true and the caller skips LLM compose — otherwise compose could
  reword a title/number (the anti-fabrication hard line). This is the exact pattern
  `metric_of_person` uses.
- **Push** extends `deterministic_suffix(result)` in `structured_answer.py` — the same hook that
  appends links on `entity_card` and metrics on `research_of_person`, applied AFTER LLM compose.
- **Registry** (`profile_fields.py`) is extended ONLY for recent metrics (same "labelled numeric
  metric" kind it already holds). Papers and trend are NEW skills — the registry doc explicitly
  scopes out time-series and lists, so we do not force them in.

## 4. Cluster 1 — Recent metrics (registry render rows + router qualifier)

**CORRECTION (Codex blocker):** recent metrics must NOT be aliased on new `Metric` rows.
`match_metric()` returns the FIRST alias match across all metrics, so aliasing `recent`/`since` on
`recent_citations` would make "recent h-index" match `recent_citations` before `h_index`. The
recency qualifier is parsed **separately in `router.py`**, not as a metric alias.

- **Registry:** append `recent_citations / recent_h_index / recent_i10_index` as `Metric` rows on
  the `scholar` Field **for RENDERING only, with NO aliases** (the bare-`i10`-not-aliased pattern).
  Template uses the captured since-year, never a fixed interval: e.g.
  `"{v:,} citations since {since}"` (the renderer is given `recent_since_year`).
- **Router qualifier rule:** `match_metric` first resolves the base metric (citations/h-index/i10).
  Then, if the query carries a recency qualifier — `recent`, `lately`, `last N years`, or
  `since <Y>` where **`Y == recent_since_year`** — the router swaps to the `recent_<key>` variant.
  A `since <Y>` with `Y != recent_since_year` does NOT claim the recent column (see honest-limit
  below). All-time routing is otherwise unchanged (the existing `test_profile_fields` /
  `test_router_metrics` gold stays green).
- **Honest since-year limit:** Scholar only exposes ONE "Since YYYY" column (`recent_since_year`,
  currently 2021). "citations since 2020" ≠ that column → answer honestly: "Scholar reports a
  *since-2021* figure (1,063); I don't have a since-2020 total." (If that specific year is in
  `cites_per_year`, the trend skill in §6 can give that year's count — clearly labelled as one
  year's citations, not a cumulative since-total.)
- **Pull:** "X's citations since 2021", "X's recent h-index".
- **Push:** on `research_of_person`, append recent alongside all-time ("2,791 citations; 1,063
  since 2021").
- **Activity share** (recent ÷ total) — pull-only, labelled with the captured year and only when
  BOTH values exist: "≈38% of X's citations are since 2021." Never "last 5 years" (the field is
  `recent_since_year`, not a fixed window).

## 5. Cluster 2 — Papers (new skill + push)

- **Skill** `papers_of_person(person, mode)` with `mode ∈ {most_cited, newest, current_year}`,
  reading `top_cited / newest / current_year` **via `entity.person_attrs()`** (the centralized
  per-person attrs reader — Codex nit). Returns a deterministic, rendered block:
  title · year · venue · cited_by · link (verbatim from the captured record). MUST be added to
  `_DETERMINISTIC_SKILLS` (§9) so compose never rewords a title/number.
- **Render:** "X's most-cited paper: *<title>* (<year>, <venue>) — <cited_by> citations." / a
  numbered top-N list / "X published N papers in <year>: …".
- **Router rules:** trigger on `paper(s) / publication(s) / published` + a selector
  (`most cited / top / best` → most_cited; `newest / latest / recent / this year` → newest or
  current_year; an explicit year → current_year if it's the current year, else honest "I only keep
  this year's + the newest/most-cited" — see §9).
- **Push (compact):** append the **most-cited** paper and the **newest** paper (one line each) to
  `entity_card` ("who is X") and `research_of_person` ("X's research"). Nothing more, to avoid bloat.

## 6. Cluster 3 — Trend & peak-year (new skill + honest guard)

- **Skill** `citation_trend_of_person(person)` reading `cites_per_year` (via
  `entity.person_attrs()`) and **importing the existing `scholar.all_time_peak()` helper — NOT
  reimplementing the guard** (Codex: drift here would recreate the all-time fabrication risk). Added
  to `_DETERMINISTIC_SKILLS` (§9). Answers:
  - peak / most-cited year (with the **all-time honesty guard**: claim "all-time" only when
    `peak > max(0, citations − Σchart)`, else "peak in the last N years / since <window-start>");
  - citations in a specific year (`cites_per_year[year]`, honest "I don't have <year>" if outside);
  - year-over-year change and an "accelerating?" read (compare last 2–3 years).
- **Pull:** "X's most-cited year?", "how many citations did X get in 2019?", "is X's research
  growing?".
- **Push (compact):** a single peak-year line on `research_of_person`
  ("Most-cited year: 2025 (251)."), guarded so it's only "all-time" when provable.
- **Since-last-refresh delta:** a pull route exists but, with one snapshot, returns the honest
  "I only have one snapshot so far (2026-06-30); month-to-month change will be available after the
  next refresh." It auto-works once a 2nd snapshot lands. (Annual growth above is unaffected.)

## 7. Disambiguation — the central hazard

"**most cited**" is ambiguous: most-cited **paper** (Cluster 2) vs most-cited **professor in
`<org>`** (existing `top_people_by_metric`). **CORRECTION (Codex): a paper noun must win FIRST, and
the papers route must run BEFORE the metric/person-ranking blocks** (today metric routing runs first
at `router.py:~360` and would catch "Koutis most cited paper" as a citations metric). Rules, in
order:
1. **Paper noun (`paper / publication / article / work`) present → papers cluster, evaluated before
   the metric block.** A metric/person-ranking match is SUPPRESSED when a paper noun is present.
   - person named → `papers_of_person` for that person.
   - **org/dept scope but no person ("most cited paper in CS") → honest decline**: cross-person
     paper ranking is fast-follow (§2), so reply "I can give a specific professor's most-cited
     paper; ranking papers across a department isn't available yet" — NOT a professor ranking.
2. No paper noun, has `professor / researcher / faculty / who` or an org scope → **person ranking**
   (unchanged `top_people_by_metric`).
3. Bare "most cited", neither noun, no org → existing NJIT-wide person-ranking default + nudge,
   UNCHANGED.
Every rule gets explicit gold tests (including named-person paper asks, which existing tests do NOT
cover — `test_router_metrics:140` only protects bare "most cited paper" falling through). The router
change ships with a **no-regression gate** against the existing metric/person-ranking tests.

## 8. Push policy — deliberately compact

Push appends ONLY the 2–3 highest-signal lines, never the full catalog:
- `entity_card` ("who is X"): existing links + **most-cited paper** + **newest paper**.
- `research_of_person` ("X's research"): existing metrics + **recent metric** + **peak year** +
  **most-cited paper**.
Everything else is pull-only. Each pushed line is deterministic and individually omitted when its
datum is absent (honest-partial), so a thin profile degrades gracefully.

**Integration reality (Codex):** `deterministic_suffix()` today returns ONE optional string and
only knows links for `entity_card` + metrics for `research_of_person`
(`structured_answer.py:~314`). This change makes it **compose a multi-line suffix** (the existing
link/metric line(s) PLUS the new paper/peak lines), not just add a return path. The existing
exact-string suffix tests (`test_structured_profiles.py:84,93`) must be updated to include the new
lines AND assert the prior link/metric lines still appear.

## 9. Anti-fabrication & honest limits (hard line)

- All numbers/titles/links rendered **deterministically**; `is_deterministic` skips LLM compose.
- **Papers are the captured highlight set only** (5 cited + 5 newest + ≤10 this-year), NOT all 65.
  A "paper on `<topic>`" or "X's 7th paper" beyond the set returns an honest "I track X's most-cited,
  newest, and this-year papers" — never a guessed title.
- **Peak-year** uses the window-reconciliation guard before any "all-time" claim.
- **Delta** honestly reports "one snapshot so far" until a 2nd exists.
- **Coverage:** a person without Scholar data → the skill declines honestly (no fabrication); the
  464 faculty without a Scholar URL are simply not covered, as designed.

## 10. Testing (TDD)

- Pull skills tested on real captured fixtures (Koutis/Bader/Dindoost attrs): most-cited paper,
  newest, this-year (incl. empty), peak-year (incl. the all-time guard true/false), citations-in-year
  (present/absent), recent-metric routing with/without the qualifier.
- Router gold tests for §7 disambiguation — **incl. named-person paper asks ("Koutis most-cited
  paper" → papers, NOT citations metric)** and "most cited paper in CS" → honest decline — plus a
  no-regression run against existing `test_router_metrics` / `test_profile_fields` (the
  over-answer-leak gate).
- `_DETERMINISTIC_SKILLS` test: `papers_of_person` / `citation_trend_of_person` results return
  `is_deterministic == True` (compose skipped).
- Recent-qualifier test: "recent h-index" → `recent_h_index` (NOT `recent_citations`); "since 2020"
  (≠ recent_since_year) → honest, not the 2021 column.
- Push tests: the multi-line suffix appears verbatim after compose, each line omitted when its datum
  is missing, AND the existing link/metric suffix lines still appear (`test_structured_profiles`
  updated, not broken).

## 11. Goals checklist (shipped / deferred)

| Goal | Status |
|---|---|
| Recent metrics (pull + push) | **DEFERRED → fast-follow** (needs a registry `default_render=False` flag so recent values don't pollute the default render + per-person dynamic since-year; punted to do it right vs. a rushed inaccurate render — loudly flagged, not silently dropped) |
| Recent routing via router qualifier (NOT aliases) | deferred with recent metrics |
| Since-last-refresh delta route | deferred with recent metrics (also needs ≥2 snapshots) |
| Papers most-cited/newest/this-year (pull) | ship |
| Papers compact push (most-cited + newest) | ship |
| Trend / peak-year (pull) + honest guard | ship |
| Peak-year compact push | ship |
| Disambiguation rules + no-regression gate | ship |
| Cross-person rankings (most-cited paper in org, fastest-growing, dept feed) | fast-follow |
| Co-authors (pull) — captured, not surfaced this build | fast-follow |
| New skills registered in `_DETERMINISTIC_SKILLS` (anti-fab) | ship |
| Recent routing via router qualifier (NOT aliases) | ship |
| Paper-noun precedence + org-paper honest decline | ship |
| Multi-line push suffix, existing lines preserved | ship |
| X-vs-Y comparison; advisor area+metric combo | fast-follow |

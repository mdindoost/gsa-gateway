# Metric Queries — Registry-Driven Scholar Metric Retrieval

> **Status:** Design — REVISED per senior-eng + RAG/researcher review (2026-06-19); awaiting
> Mohammad's final sign-off to build, per the EXPERT-REVIEW HARD GATE.
> **Date:** 2026-06-19
> **Author:** Claude (Opus 4.8) with Mohammad Dindoost.
> **Related:** `2026-06-19-person-external-profiles-design.md` (the profiles feature this extends),
> `v2/core/people/profile_fields.py` (the registry), `v2/core/retrieval/{router,skills,entity,structured_answer}.py`.

## Problem

Scholar metrics (citations / h-index / i10-index) are stored on every enriched Person node
(`attrs.profiles.scholar.{citations,h_index,i10_index,updated_at}`) — ~49 people today — but they
are **not queryable**. Three real user questions all failed:

| Question | What happened | Why |
| --- | --- | --- |
| "Koutis citation" | Deflected ("contact a GSA officer") | No metric trigger word in the router; falls to semantic RAG; metrics aren't in KB text, so RAG finds nothing. |
| "What is koutis personal website" | Worked — by luck | Fell to RAG too, but the website happens to be plain text in a KB chunk. Citations were never written to text, so they don't get this luck. |
| "who has the most citation in cs department" | Deflected | No aggregate/superlative skill exists; metrics aren't sortable anywhere; RAG can't see them. |

Root cause: metrics are a **second-class** facet. They surface in exactly one place — appended as a
verbatim suffix to a `research_of_person` answer, **gated behind the person having research areas**
(`structured_answer.deterministic_suffix`). They have no trigger words, no standalone single-person
answer, and no aggregation. Koutis himself has `citations: 2774, h_index: 26, i10_index: 35` and 4
research-area edges — the data exists; retrieval simply cannot reach it with natural phrasing.

## Goal

Make metrics a **first-class, registry-driven** retrieval facet — the same status research areas,
roles, and departments already have — closing all three failures:

1. **Single-person:** "Koutis citations", "what's Koutis's h-index", "how many citations does Oria have".
2. **Org ranking:** "who has the most citations in CS", "top 5 by h-index in YWCC".

Built **registry-driven** (not by hard-coding `"citation"`): `profile_fields.py` is already the single
source of truth for which metric fields exist and how they render. This change makes the **router and
skills read that same registry**, so adding a future metric (e.g. a `publications` count) makes it
instantly askable *and* rankable with **zero router/skill changes** — Mohammad's "one uniform
standard, no band-aid" rule extended to metrics.

### Non-goals (YAGNI)

- No general aggregate/facet planner (ranking over arbitrary facets like research-area counts). Defer
  until a second facet actually needs ranking; today it would overlap the existing `area_counts` /
  `count_people_by_research_area` skills for no benefit.
- No schema change. Metrics stay in `nodes.attrs` JSON; ranking reads them via `json_extract`.
- No new data sourcing. Coverage growth (more people with metrics) is a separate, already-tracked
  thread (personal-website link sourcing). This work only makes existing data reachable.
- No LLM involvement in numbers. Metric values are rendered deterministically (see below).

## Design (Approach 1: two dedicated registry-driven skills)

Mirrors the existing skill pattern (`officers_in_org` / `people_in_org` / `faculty_areas_in_department`).

### 1. Registry: metric aliases + matcher (`v2/core/people/profile_fields.py`)

The `Metric` dataclass gains an `aliases: tuple[str, ...]` field — the natural-language words that name
that metric. The registry stays the single source of truth.

```python
@dataclass(frozen=True)
class Metric:
    key: str
    template: str
    aliases: tuple[str, ...] = ()   # NEW

# in PROFILE_FIELDS, the scholar field:
metrics=(
    Metric("citations", "{v:,} citations", aliases=("citation", "citations", "cited")),
    Metric("h_index",   "h-index {v}",     aliases=("h-index", "h index", "hindex")),
    Metric("i10_index", "i10-index {v}",   aliases=("i10-index", "i10 index")),
)
```

**Alias breadth (RAG review B3) — deliberately conservative.** Bare `i10` is DROPPED (it matches
immigration "form i10"); `cite` is DROPPED (verb form: "how do I cite a paper", and "papers Koutis
cited" is a semantic inversion — references *by* X, not citations *to* X). `cited` is KEPT because it
carries the most natural single-person phrasing ("how many times has Koutis been cited", "is Koutis
highly cited"). Aliases are matched word-boundary anchored so `i10-index` never fires inside `i1000`.

Two new pure helpers (registry is the only place that knows metric words):

- `metric_fields() -> list[(field_key, Metric)]` — every metric across all fields (today: scholar's 3).
- `match_metric(text: str) -> tuple[str, Metric] | None` — lower-cased word-boundary scan of `text`
  against every metric's aliases; returns `(field_key, Metric)` of the first hit, else `None`.

`match_metric` returns the `field_key` too (e.g. `"scholar"`) so the skills know the dotted JSON path
`profiles.<field_key>.<metric.key>` without hard-coding "scholar".

### 2. Router: one metric branch (`v2/core/retrieval/router.py`)

A new branch, placed **before** the generic research/entity-card branches so a metric word wins over a
bare-name match, but **after** the precise area branches (a metric question is never an area question):

```
m = profile_fields.match_metric(q)
if m is not None:
    field_key, metric = m
    # (a) ranking — an org resolves AND a superlative/top-N cue is present.
    #     resolve_org returns the ROOT (NJIT) org for "at NJIT" -> a university-wide ranking
    #     (option (a), Mohammad 2026-06-19) — the honest-partial caveat carries the huge denominator.
    org = resolve_org(conn, q)                      # existing org resolver; resolves the root for NJIT
    if org is not None and _RANK_CUE.search(q):
        n = _parse_topn(q)                          # "top 5" -> 5; default 1 for "most/highest"
        return Route("top_people_by_metric",
                     {"org_id": org.org_id, "org_name": org.name,
                      "field_key": field_key, "metric_key": metric.key, "n": n})
    # (b) single person — reuse the EXTRACTED person resolver (see below).
    person = _resolve_person(conn, q, named)        # full name (named==1) OR unambiguous surname
    if isinstance(person, Route):                   # ambiguous surname -> person_disambig Route
        return person
    if person is not None:
        return Route("metric_of_person",
                     {"entity_id": person["entity_id"], "name": person["name"],
                      "field_key": field_key, "metric_key": metric.key})
    # neither resolved -> DO NOT return; fall through so the existing person/RAG branches still run.
```

- `_RANK_CUE = re.compile(r"\b(most|top|highest|ranked?|rank)\b")` — **narrowed** (RAG review B2):
  dropped `leading|largest|greatest` (they never naturally rank citations, only add false positives);
  `top` covers both "top professor" guard-free (still needs a metric word + org to reach here) and
  "top 5". `_parse_topn` parses "top N" / "N most" → N; "most"/"highest" with no number → 1.
- **Person resolution is EXTRACTED, not duplicated (senior review S1).** The surname-fallback loop is
  currently inline in `route()` twice (`router.py:289-295` and `311-320`); pull it into one helper
  `_resolve_person(conn, q, named) -> dict | Route | None` that returns `{entity_id, name}` for a
  single match, a `person_disambig` `Route` for ≥2, or `None`. The research branch and the new metric
  branch both call it. Ambiguous surname → `person_disambig`, exactly like the research branch.
- **Fall-through correctness (senior review S3).** If a metric word appears but neither an org+rank-cue
  nor a resolvable person is found, the branch must **NOT `return None`** — that would skip the existing
  entity-card / surname / RAG branches below it. It simply does nothing and lets control continue. So
  "citation policy" / "how do I cite a paper" (metric-ish word, no org, no surname) flows to RAG via the
  normal path; "Koutis" with a metric word but handled by neither sub-case still reaches `entity_card`.

### 3. Skills

**Single person** — `metric_of_person` in `entity.py` (it owns per-person reads), but it **reuses the
existing `structured_answer._person_attrs(conn, entity_id)`** rather than adding a second JSON reader
(senior review S5):

```python
def metric_of_person(conn, entity_id, field_key, metric_key=None) -> dict:
    """{name, field_key, found: {metric_key: value, ...}, all: {...}, updated_at}.
    Reads attrs via the existing _person_attrs helper. `found` is the asked metric (or all metrics
    for the field if metric_key is None); `all` is every metric present for the field (so a partial
    miss can still offer what we DO have). Honest-empty (found={}) when absent."""
```

Rendering is deterministic via `render_metrics` (below), which gains an optional `only=metric_key`
parameter so the registry stays the single source of truth for formatting (senior review S5).

**Org ranking** — `top_people_by_metric` in `skills.py` (it owns org-scoped queries):

```python
def top_people_by_metric(conn, org_id, field_key, metric_key, n) -> dict:
    """Top-n DISTINCT active people in the org SUBTREE who HAVE this metric, sorted desc.
    Returns {ranked: [(name, value), ...], total_in_org, with_metric}."""
```

- Org scope = `org_descendants(conn, org_id)` (the subtree helper `people_by_research_area` uses), so
  "CS department" ranks CS people, "YWCC" ranks the whole college, and the **root org ranks all of NJIT**
  (option (a)).
- Membership = any active `has_role` edge into an org node whose `attrs.org_id` is in the subtree (same
  join as `people_in_org`). We count **people of any role** and call them "people" (NOT "faculty") in
  the answer — the join is all-roles, so the noun and the denominator agree (senior review B2).
- **DISTINCT-per-person is in the SQL, not just prose (senior review B1).** A person with 2+ `has_role`
  edges in the subtree (faculty + grad-director, joint appts) must count ONCE. The ranking SQL
  `GROUP BY p.id` (taking `MAX` of the metric, identical per person) and both counts use
  `COUNT(DISTINCT p.id)`:

  ```sql
  -- ranked rows
  SELECT p.name, json_extract(p.attrs,'$.profiles.'||?||'.'||?) AS v
  FROM edges e JOIN nodes p ON p.id=e.src_id
               JOIN nodes o ON o.id=e.dst_id AND o.is_active=1
  WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1
    AND json_extract(o.attrs,'$.org_id') IN (<subtree ids>)
    AND v IS NOT NULL
  GROUP BY p.id
  ORDER BY CAST(v AS INTEGER) DESC, p.name ASC
  LIMIT ?;
  -- with_metric = same query without LIMIT, COUNT(DISTINCT p.id)
  -- total_in_org = same membership join (no metric filter), COUNT(DISTINCT p.id)
  ```

  `json_extract` with a **concatenated path** (`'$.profiles.'||?||'.'||?`) is valid on our SQLite
  (verified 3.45.1 in the senior review) — registry-driven, no literal "scholar". The `json_extract`
  runs only over the org-subtree membership (bounded by the join — at most a few hundred rows), so **no
  index is warranted** and there is no N+1 (single query per count).
- `total_in_org` (all distinct people in the subtree) and `with_metric` (distinct people who had the
  metric) BOTH feed the honest-partial wording — `total_in_org` is the number that conveys the gap and
  must be printed.

### 4. Wiring + rendering (`structured_answer.py`)

Add `metric_of_person` and `top_people_by_metric` to `run()` and `format_answer()`. **Numbers are
rendered deterministically** — built in Python via `profile_fields`' templates, NOT handed to the LLM
to restate (same principle as `deterministic_suffix` / `render_metrics`). These two skills do **not**
go through `compose_from_rows`; their `format_answer` output IS the final answer. This guarantees no
reworded or invented numbers.

- `metric_of_person` →
  - has it: *"Ioannis Koutis — 2,774 citations, h-index 26, i10-index 35 (as of 2026-06)."*
    (single asked metric → just that one; bare "citations" with no specific metric → all of scholar's.)
  - **partial miss offers what we have (RAG review S5)** — asked for h-index, only citations on file:
    *"I don't have an h-index on file for Ioannis Koutis — I do have his citation count: 2,774."*
    (built from `metric_of_person`'s `all` bag; never reads "no metrics" when some exist.)
  - honest-empty (nothing at all): *"I don't have Scholar metrics on file for Ioannis Koutis."*
- `top_people_by_metric` → **honest-partial header is mandatory and MUST print `total_in_org` plus a
  literal "not a full ranking" caveat whenever `with_metric < total_in_org` (RAG review B1).** The
  earlier draft ("of the 41 CS faculty…") is REJECTED — it dropped the denominator and mis-said
  "faculty". Wording:
  - n=1, partial coverage: *"I only have Scholar citation metrics for 41 of Computer Science's ~250
    people, so this isn't a full ranking. Among those 41, the most cited is Ioannis Koutis (2,774
    citations)."*
  - top-N, partial coverage: *"Among the 41 of ~250 Computer Science people I have Scholar metrics for
    (not a full ranking), the top 5 by citations are: 1. … 2. …"*
  - **n>actual (RAG review S3)** — asked top 5 but only 3 have metrics: render the ACTUAL count, never
    a "Top 5" header over 3: *"You asked for the top 5, but I only have citation metrics for 3 of
    Computer Science's ~250 people: 1. … 2. … 3. …"*
  - `with_metric == total_in_org` (rare at current coverage): the "not a full ranking" caveat may be
    dropped.
  - none in org have metrics: *"I don't have Scholar metrics on file for anyone in Computer Science."*
  - **University-wide (option (a))** — the same wording with the root org: *"I only have Scholar
    citation metrics for 49 of NJIT's ~1,076 people, so this is far from a full ranking. Among those
    49, the most cited is …"* — the loud denominator is what makes a 49-of-1076 ranking honest.
  - This is the anti-fabrication (honest-partial) invariant applied to aggregation: the reader must
    learn the **denominator gap**, not just the numerator.

### 5. Surfacing notes

- These are **structured** answers → **no feedback buttons** (consistent with the existing rule:
  buttons = RAG, no buttons = structured).
- No "Hi there!" greeting (that opener is for person identity/"tell me about" answers, not metric
  lookups).
- A heads-up line is **not** warranted (metrics aren't immigration/billing/funding); skip headsup.

## Data flow

```
"who has the most citations in CS"
  → router: match_metric -> ("scholar", citations); resolve_org -> CS; _RANK_CUE hit; n=1
  → Route("top_people_by_metric", {...})
  → skills.top_people_by_metric: org_descendants(CS), GROUP BY p.id, json_extract scholar.citations,
        ORDER BY CAST(v AS INT) DESC, name LIMIT 1; total_in_org=250 people, with_metric=41
  → structured_answer.format_answer: honest-partial sentence printing BOTH 41 and ~250 + "not a full
        ranking" caveat (since 41 < 250)
  → final answer (no LLM compose, no buttons, no greeting)
```

```
"Koutis citations"
  → router: match_metric -> ("scholar", citations); no rank cue; surname "koutis" -> 1 person
  → Route("metric_of_person", {entity_id, field_key=scholar, metric_key=citations})
  → entity.metric_of_person: read attrs.profiles.scholar.citations
  → format_answer: "Ioannis Koutis — 2,774 citations (as of 2026-06)."
```

## Error handling / edge cases

- **Ambiguous surname** ("Wang citations") → `person_disambig` (existing), same as the research branch.
  Known v1 UX gap (RAG review N2): the disambiguation re-prompt drops the metric intent, so the user
  re-asks after picking — acceptable; carrying intent needs conversational state the router lacks.
- **Metric word but no person/org resolves** ("citation policy", "how do I cite a paper") → branch does
  nothing and control falls through to the normal person/RAG path. `_RANK_CUE` + an actual org are both
  required for ranking, so "most cited paper" (no org) does not trigger a ranking.
- **"most cited research area in X"** (RAG review S4) → `_ENUM_AREAS` fires first (metric branch sits
  AFTER the area branches), so it routes to the area skill, not a metric ranking. A precedence test
  pins this so a future reorder can't regress it.
- **Person has the field but not that metric** (e.g. citations but no i10) → render the partial-miss
  line that OFFERS the metrics present (RAG review S5), never a flat "no metrics".
- **Ties in ranking** → stable secondary sort by name. For n>1, `LIMIT n` cutting a tie is acceptable.
  For **n=1** a top-value tie is a correctness risk ("the most cited is X" when X and Y tie) — detect a
  tie at the top value and name all tied people (or state the tie) rather than arbitrarily picking one
  (senior review S2). Tested.
- **Value stored as a string** (older rows) → `CAST(... AS INTEGER)` in SQL; `set_person_profiles`
  already coerces on write, so this is belt-and-suspenders.
- **updated_at** rendered as "as of <YYYY-MM>" when present, omitted otherwise.

## Testing (TDD)

Unit (no network, fixture DB with a handful of people carrying/ lacking metrics):

- `profile_fields`: `match_metric` hits each KEPT alias incl. hyphen/space variants (`h-index`/`h index`
  /`hindex`, `i10-index`/`i10 index`), is case-insensitive, returns the right `(field_key, Metric)`;
  returns `None` for non-metric text AND for the **dropped** aliases (`i10` alone, `cite`) — e.g.
  "form i10" and "how do I cite a paper" must NOT match; `metric_fields()` lists scholar's 3.
- `entity.metric_of_person`: has-all, single asked metric, **partial (has citations, asked h-index →
  `all` carries citations so the offer line works)**, has-none (honest-empty), unknown person; confirms
  it reuses `_person_attrs` (no second JSON path).
- `skills.top_people_by_metric`: ordering desc; `n` limit; subtree scope (dept vs college vs **root =
  university-wide**); `with_metric` vs `total_in_org` via `COUNT(DISTINCT p.id)`; **two-edge person
  counted once** (the B1 regression test); empty-org honest case; **n=1 top-value tie names all tied**;
  top-N where actual < n.
- `router.route`: the 3 failing questions; "Koutis citations" → person; "most citations in CS" →
  ranking; "top 5 by h-index in YWCC" → ranking n=5 h_index; "who is the most cited at NJIT" → ranking
  on the root org; ambiguous surname → disambig; "citation policy" / "how do I cite a paper" → fall
  through (NOT a metric route); **"most cited research area in CS" → area skill, not metric (precedence
  test, S4)**; a metric word + full name handled by neither sub-case still reaches `entity_card`
  (fall-through, S3).
- `structured_answer.format_answer`: deterministic strings incl. the mandatory honest-partial header
  printing BOTH `with_metric` and `total_in_org` + the "not a full ranking" caveat when
  `with_metric < total_in_org`; the partial single-person offer line; the actual-count top-N line;
  numbers via the registry templates (commas, labels); **no LLM call on these skills**, and
  `deterministic_suffix` does not double-fire on them.

Eval (grow the correctness suite — project invariant): add to `eval/questions.txt` under a new
`# metrics` header: "Koutis citations", "what is Koutis's h-index", "who has the most citations in CS",
"top 5 most cited faculty in YWCC", "who is the most cited professor at NJIT", and a known honest-empty
case.

## Files touched

- `v2/core/people/profile_fields.py` — `Metric.aliases` (trimmed set), `metric_fields()`,
  `match_metric()`, and `render_metrics(attrs, only=metric_key)` (new optional param).
- `v2/core/retrieval/router.py` — metric branch + narrowed `_RANK_CUE` + `_parse_topn`; **extract
  `_resolve_person(conn, q, named)`** from the two inline surname loops and call it from both the
  research branch and the metric branch (no duplication).
- `v2/core/retrieval/entity.py` — `metric_of_person` (reuses `_person_attrs`).
- `v2/core/retrieval/skills.py` — `top_people_by_metric` (GROUP BY p.id, COUNT(DISTINCT p.id)).
- `v2/core/retrieval/structured_answer.py` — wire both into `run`/`format_answer` (deterministic, no
  compose); confirm `deterministic_suffix` does not also fire for them.
- `v2/tests/` — new tests per above.
- `eval/questions.txt` — `# metrics` questions.
- `CLAUDE.md` — note metrics as a first-class queryable facet under Retrieval.

No schema change, no migration, no data write → **no bot restart needed for the data**; this is code,
so a restart is needed to load the new retrieval code (`bash scripts/restart.sh`).

## Rollout

Per the EXPERT-REVIEW HARD GATE: this design → senior-engineer review **and** RAG/LLM-researcher review
→ Mohammad approves → TDD build → show diff → Mohammad signs off → commit + restart + eval.

# Metric Queries — Registry-Driven Scholar Metric Retrieval

> **Status:** Design (awaiting expert review + Mohammad's approval per the EXPERT-REVIEW HARD GATE).
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
    Metric("citations", "{v:,} citations", aliases=("citation", "citations", "cited", "cite")),
    Metric("h_index",   "h-index {v}",     aliases=("h-index", "h index", "hindex")),
    Metric("i10_index", "i10-index {v}",   aliases=("i10", "i10-index", "i10 index")),
)
```

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
    # (a) org ranking — an org resolves AND a superlative/top-N cue is present
    org = resolve_org(conn, q)                      # existing org resolver
    if org is not None and _RANK_CUE.search(q):     # "most|highest|top|ranked|leading|N most"
        n = _parse_topn(q)                          # "top 5" -> 5; default 1 for "most/highest"
        return Route("top_people_by_metric",
                     {"org_id": org.org_id, "org_name": org.name,
                      "field_key": field_key, "metric_key": metric.key,
                      "metric_label": ..., "n": n})
    # (b) single person — reuse the SAME person resolution the card/research branches use
    person = <full-name (named==1) OR unambiguous-surname fallback>
    if person is not None:
        return Route("metric_of_person",
                     {"entity_id": ..., "name": ..., "field_key": field_key,
                      "metric_key": metric.key})
    # neither resolved -> fall through (RAG), no fabrication
```

- `_RANK_CUE = re.compile(r"\b(most|highest|top|leading|ranked|largest|greatest)\b")`.
- `_parse_topn` parses "top N" / "N most" → N; "most"/"highest" with no number → 1.
- Person resolution **reuses** the existing logic (the `named`/`_RESEARCH_CUE` surname fallback in the
  research branch and `persons_by_lastname`) — no new resolver. Ambiguous surname → `person_disambig`
  (existing skill), exactly like the research branch.
- If a metric word appears but neither an org+rank-cue nor a resolvable person is found, **fall through
  to RAG** (return `None`) — never invent.

### 3. Skills

**Single person** — in `entity.py` (it already owns per-person reads):

```python
def metric_of_person(conn, entity_id, field_key, metric_key=None) -> dict:
    """{name, field_key, found: {metric_key: value, ...}, updated_at} read straight from
    attrs.profiles[field_key]. If metric_key is None, return ALL metrics for that field.
    Honest-empty (found={}) when the person has no such metric."""
```

Reads `nodes.attrs` JSON in Python (single row — no need for SQL json_extract). Returns the raw
numbers; rendering is deterministic (below).

**Org ranking** — in `skills.py` (it owns org-scoped queries):

```python
def top_people_by_metric(conn, org_id, field_key, metric_key, n) -> dict:
    """Top-n active people in the org SUBTREE who HAVE this metric, sorted desc.
    Returns {ranked: [(name, value), ...], total_in_org, with_metric}."""
```

- Org scope = `org_descendants(conn, org_id)` (the subtree helper `people_by_research_area` uses), so
  "CS department" ranks CS people and "YWCC" ranks all YWCC-college people.
- Membership = any active `has_role` edge into an org node whose `attrs.org_id` is in the subtree
  (same join as `people_in_org`), DISTINCT per person.
- Metric value via `json_extract(p.attrs,'$.profiles.'||?||'.'||?, ...)` — `field_key`+`metric_key`
  bound as parameters (registry-driven, no literal "scholar"). `WHERE value IS NOT NULL`, `ORDER BY
  CAST(value AS INTEGER) DESC LIMIT n`.
- `total_in_org` = distinct people in the subtree; `with_metric` = how many had the metric. These two
  numbers drive the honest-partial wording.

### 4. Wiring + rendering (`structured_answer.py`)

Add `metric_of_person` and `top_people_by_metric` to `run()` and `format_answer()`. **Numbers are
rendered deterministically** — built in Python via `profile_fields`' templates, NOT handed to the LLM
to restate (same principle as `deterministic_suffix` / `render_metrics`). These two skills do **not**
go through `compose_from_rows`; their `format_answer` output IS the final answer. This guarantees no
reworded or invented numbers.

- `metric_of_person` →
  - has it: *"Ioannis Koutis — 2,774 citations, h-index 26, i10-index 35 (as of 2026-06)."*
    (single asked metric → just that one; bare "citations" with no specific metric → all of scholar's.)
  - honest-empty: *"I don't have Scholar metrics on file for Ioannis Koutis."*
- `top_people_by_metric` → **honest-partial header is mandatory**:
  - *"Of the 41 CS faculty I have Scholar metrics for, the most cited is Ioannis Koutis (2,774 citations)."*
  - top-N: *"Top 5 CS faculty by citations (of 41 with Scholar metrics on file): 1. … 2. …"*
  - none in org have metrics: *"I don't have Scholar metrics on file for anyone in Computer Science."*
  - The phrase "I have Scholar metrics for" / "with Scholar metrics on file" makes the partial coverage
    explicit so the answer is never read as ranking the whole department. This is the anti-fabrication
    (honest-partial) invariant applied to aggregation.

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
  → skills.top_people_by_metric: org_descendants(CS), DISTINCT members, json_extract scholar.citations,
        ORDER BY value DESC LIMIT 1; total_in_org=250, with_metric=41
  → structured_answer.format_answer: deterministic honest-partial sentence
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
- **Metric word but no person/org resolves** ("citation policy", "how do I cite a paper") → fall to
  RAG. `_RANK_CUE` + an actual org are both required for ranking, so "most cited paper" (no org) does
  not trigger a ranking.
- **Person has the field but not that metric** (e.g. scholar URL but no i10) → honest-empty for the
  asked metric; if "citations" generally, render whichever metrics are present.
- **Ties in ranking** → stable secondary sort by name; `LIMIT n` may cut a tie arbitrarily — acceptable
  (note in tests; revisit only if it matters).
- **Value stored as a string** (older rows) → `CAST(... AS INTEGER)` in SQL; `set_person_profiles`
  already coerces on write, so this is belt-and-suspenders.
- **updated_at** rendered as "as of <YYYY-MM>" when present, omitted otherwise.

## Testing (TDD)

Unit (no network, fixture DB with a handful of people carrying/ lacking metrics):

- `profile_fields`: `match_metric` hits each alias, is case-insensitive, returns the right
  `(field_key, Metric)`, and returns `None` for non-metric text; `metric_fields()` lists scholar's 3.
- `entity.metric_of_person`: has-all, has-some, has-none (honest-empty), unknown person.
- `skills.top_people_by_metric`: ordering desc, `n` limit, subtree scope (college vs dept), `with_metric`
  vs `total_in_org` counts, empty-org honest case, tie behavior.
- `router.route`: each of the 3 failing questions routes correctly; "Koutis citations" → person;
  "most citations in CS" → ranking; ambiguous surname → disambig; "citation policy" → None (RAG);
  "top 5 by h-index in YWCC" → ranking with n=5 and the h_index field.
- `structured_answer.format_answer`: deterministic strings incl. the mandatory honest-partial header;
  numbers formatted via the registry templates (commas, labels); no LLM call on these skills.

Eval (grow the correctness suite — project invariant): add to `eval/questions.txt` under a new
`# metrics` header: "Koutis citations", "what is Koutis's h-index", "who has the most citations in CS",
"top 5 most cited faculty in YWCC", and a known honest-empty case.

## Files touched

- `v2/core/people/profile_fields.py` — `Metric.aliases`, `metric_fields()`, `match_metric()`.
- `v2/core/retrieval/router.py` — metric branch + `_RANK_CUE` + `_parse_topn`.
- `v2/core/retrieval/entity.py` — `metric_of_person`.
- `v2/core/retrieval/skills.py` — `top_people_by_metric`.
- `v2/core/retrieval/structured_answer.py` — wire both into `run`/`format_answer` (deterministic).
- `v2/tests/` — new tests per above.
- `eval/questions.txt` — `# metrics` questions.
- `CLAUDE.md` — note metrics as a first-class queryable facet under Retrieval.

No schema change, no migration, no data write → **no bot restart needed for the data**; this is code,
so a restart is needed to load the new retrieval code (`bash scripts/restart.sh`).

## Rollout

Per the EXPERT-REVIEW HARD GATE: this design → senior-engineer review **and** RAG/LLM-researcher review
→ Mohammad approves → TDD build → show diff → Mohammad signs off → commit + restart + eval.

# P2.5 — Research-Area Facet — Design Spec

**Goal:** turn the now-clean `research_areas` tags into a **structured, enumerable
dimension** so the structured layer can answer questions P2 can't — "what research areas
does CS cover?", "which areas have the most faculty?", "who *lists* X as a research area?"
— while NOT regressing the recall-oriented "who works on X" path.

Builds on the data-quality thread (clean `research_areas` at the source) and the P2
expansion map. Locked decisions (from brainstorming 2026-06-14):
- **Both** enumerate/aggregate **and** a precise exact-tag match leg.
- Tags stored **in item metadata as a list** (no new table).
- **Case-fold grouping only**; synonym matching stays at query time via the P2 expansion map.

---

## 1. Architecture & data model

The clean area list already exists as `EntityRecord.research_areas`. Today `decompose`
joins it into a `"; "`-separated string for the `research_areas` item *content*; the
discrete list is lost. P2.5 adds the list to that item's **metadata**:

```jsonc
// research_areas knowledge_item, metadata:
{ "entity_id": "...", "verified": true, "natural_key": "...:research_areas:main",
  "areas": ["data mining", "machine learning", "deep learning", ...] }   // NEW
```

Skills read it with SQLite `json_each(metadata, '$.areas')` (core SQLite, works on the
plain `sqlite3` connection the structured path already uses — no vec, no new dep, no table).
Populated on the next re-ingest (run **with `--overview`** so overviews aren't dropped —
see the data-quality spec's overview gotcha).

**Why metadata-list over a normalized table:** the list already exists; `json_each` gives
distinct/count/exact-match for free; multi-tenant with zero per-entity schema. A table would
add a migration + a sync path for no capability gain at this scale.

## 2. Components — new skills (`v2/core/retrieval/skills.py`)

All scoped to an org subtree (reuse `org_descendants`), all over `is_active=1`
`research_areas` items, all reading `metadata.areas`.

- **`areas_in_org(conn, org_id) -> list[str]`** — distinct areas, **case-folded** for
  grouping, displayed in a canonical casing (the most frequent surface form, ties broken
  alphabetically). The genuinely new capability ("what areas does CS cover?").
- **`area_counts(conn, org_id) -> list[tuple[str, int]]`** — `(canonical_area, faculty_count)`
  sorted by count desc then name. Counts **distinct entity_ids** per case-folded area (a
  professor listing an area twice counts once). ("which areas have the most faculty?")
- **`people_by_area_tag(conn, area, org_id) -> list[tuple[str, str]]`** — `(name, entity_id)`
  for faculty whose `metadata.areas` contains the area, matched **case-folded** and
  **P2-expanded** (`expand_area(area)` so "ml"/"llm" hit the canonical tags). Precise,
  lower-recall — answers "who *lists* X as a research area".

Shared helper canonicalizes casing so `areas_in_org`, `area_counts`, and
`people_by_area_tag` can never disagree on what a "tag" is.

## 3. Data flow & routing (`router.py`, `structured_answer.py`)

- **Router additions (deterministic, conservative):**
  - "what/which research areas …", "list … areas …", "areas in <org>" → `areas_in_org`.
  - "which/what areas … most/popular …", "how many people per area", "areas by count" →
    `area_counts`.
  - "who **lists** X as a research area" / explicit area-tag phrasing → `people_by_area_tag`.
- **Unchanged:** "who works on X" and "how many work on X" stay on the existing
  `people_by_research_area` / `count_people_by_research_area` (FTS + expansion). Rationale:
  the facet covers only ~26/83 faculty, so routing the recall questions to it would
  under-count (ML ~9 vs ~23) **and** break the P1 "list == count" invariant. The facet's
  unique value is enumeration/aggregation; the recall path stays complete and internally
  consistent.
- `structured_answer.run/format_answer` gain formatting for the three new result shapes
  (area list; `area — N` count lines; people list), reusing the existing deterministic
  honest-empty + `compose_from_rows` rephrasing path.

## 4. Coverage honesty

Enumeration/aggregation reflect only faculty who **list** discrete areas (~26/83). The
formatted answer states this basis (e.g. "Across the N CS faculty who list research areas:
…") so a count is never mistaken for the whole department. This is the data-coverage limit
(the parked enrichment track), surfaced honestly, not hidden.

## 5. Error handling / edge cases

- No `metadata.areas` on a card (older items pre-re-ingest, or emptied pure-prose profiles)
  → contributes nothing (skills treat missing/empty as no tags). Never an error.
- Org with zero tagged faculty → empty list/counts → honest "no research areas listed".
- `people_by_area_tag` with an unmapped, unknown area → `expand_area` returns `[area]` →
  exact case-fold match only → possibly empty (honest).
- Determinism preserved: pure SQL + the fixed expansion map → same question, same answer.

## 6. Testing (TDD)

- **`decompose`** (`test_decompose.py`): a record with `research_areas=[…]` → the
  `research_areas` item's `metadata.areas` equals that list; empty list → no `areas` key (or
  `[]`), consistent with the ≥2-token extraction rule.
- **skills** (`test_skills.py`): fixture inserts `research_areas` items with
  `metadata.areas` (mixed casing, duplicates, two orgs); assert `areas_in_org` is distinct +
  case-folded + canonical-cased + org-scoped; `area_counts` counts distinct entities and
  sorts; `people_by_area_tag` matches case-fold + via `expand_area` ("ml"→"machine
  learning") + org-scoped; `area_counts` total reconciles with `areas_in_org` length.
- **router** (`test_router.py`): "what areas does CS cover" → `areas_in_org`; "which areas
  have the most faculty" → `area_counts`; "who lists graph as a research area" →
  `people_by_area_tag`; "who works on graph" → still `people_by_research_area` (unchanged).
- **structured_answer** (`test_structured_answer.py`): each new shape formats deterministically
  with the coverage-basis line; empty → honest message.

## 7. Scope / non-goals

**In:** `metadata.areas` in decompose; `areas_in_org` / `area_counts` / `people_by_area_tag`;
router + structured_answer wiring; the re-ingest (with `--overview`) to populate metadata;
tests.

**Out:** changing "who works on X" recall routing (stays FTS); a normalized table; synonym
canonicalization in storage (query-time expansion only); events/contacts skills (P3); data
enrichment for the 57 faculty without a card (coverage track); the golden-eval harness
(separate, will add these question shapes when built).

## 8. Risks

- **Re-ingest to populate `metadata.areas`** — must pass `--overview` (and the no-web choice)
  to avoid the overview-deactivation gotcha; auto-backup + spot-check as before.
- **Canonical-casing choice** (most-frequent surface form) is cosmetic; a tie just sorts
  alphabetically — never a wrong fact.
- **Coverage** bounds enumeration to ~26/83; mitigated by the explicit coverage-basis line
  so counts aren't over-read.

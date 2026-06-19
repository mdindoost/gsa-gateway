# Person External Profiles — Design

**Date:** 2026-06-19  ·  **Status:** built (storage + serving + write path) · senior-reviewed

## Goal
Attach external-profile data to people and surface it in answers:
- **Links**: Google Scholar, LinkedIn, ORCID (future), personal website.
- **Scholar metrics**: total citations, h-index, i10-index (+ `updated_at`).
- **Scholar research interests**: fed into the EXISTING `ResearchArea` nodes + `researches` edges.
- "who is X" → show the links. "X's research" → show the metrics. Faculty lists stay clean.
- Extensible: adding a new field (ORCID, …) = one registry row + the data.

Acquisition (how the data is fetched) is OUT OF SCOPE here — assume manual/dashboard entry.

## Storage (KG)
A generic bag on the Person node `attrs`:
```
attrs.profiles = {
  "scholar":  {"url", "citations", "h_index", "i10_index", "updated_at"},
  "linkedin": {"url"},
  "orcid":    {"url"},     # future = one registry row + data
  "website":  {"url"}      # alias-reads the crawler's attrs.links.website (no migration)
}
```
Metrics stored as JSON **numbers** so a future "most-cited in CS" skill is a `json_extract` +
`CAST(... AS INTEGER) ORDER BY`. Not first-classed into nodes/edges — a profile is an attribute of
a person, nothing to traverse.

## Registry (`v2/core/people/profile_fields.py`)
The single source of truth for which fields exist and how they render. Flat field *catalog*
(`Field(key, label, icon, metrics=(Metric(key, template), …), attrs_fallback=(…))`), `PROFILE_FIELDS`
list. `render_links(attrs)` and `render_metrics(attrs)` are registry-driven. Website alias-reads
`attrs.links.website` via `attrs_fallback`. It scales to more fields of the same two kinds (links,
labelled numeric metrics); it is NOT a plugin system for arbitrary render behaviours (time series,
co-author graphs) — those are out of scope.

## Serving
Surfacing is encoded by the structured skill the router already picks — no new classifier:
- `entity_card` ("who is X" / "tell me about X") → append the **links** line.
- `research_of_person` ("X research / works on") → append the **metrics** line.
- roster/list skills (`faculty_in_department`, `people_in_org`, …) → nothing.

`structured_answer.run()` attaches `links` / `metrics` (rendered from the node's `attrs`);
`structured_answer.deterministic_suffix(result)` returns the line, but ONLY when the structured
answer actually stood (card / research present) — else the query fell to RAG and nothing is added.
`message_handler._try_structured` appends the suffix to the FINAL answer **after** LLM composition
(the heads-up pattern), so URLs/numbers are rendered deterministically and never restated by the LLM.

## Provenance & safety (senior review)
- **Reconcile source-scoping (blocker, fixed `c…`)**: `reconcile_entity`'s existing-rows SELECT now
  filters by `created_by`, so a crawler re-run only reconciles crawler rows and never wipes
  `created_by='scholar'` enrichment sharing the same entity_id (the Woodruff bug class at the source
  boundary). Mirrors `people_editor`'s existing precedent. Shipped as a standalone fix.
- **Enrichment provenance**: KB items `created_by='scholar'` with source-prefixed natural keys
  (`<entity_id>:scholar:…`); `researches` edges from Scholar tagged `source='scholar'`.
- **Departure hygiene**: a fully-departed person's KB is dropped across ALL sources (not just
  `created_by='crawler'`) so enrichment can't orphan against a deactivated node.
- **No LLM restatement of metrics** — deterministic rendering only (the one hallucination risk).
- **Concurrency**: enrichment writes go through `local_server` / gated CLI (single writer); the
  attrs read-modify-write stays within the caller's transaction (no key clobber).

## Write path
`people_editor.set_person_profiles(conn, person_key, profiles, replace=False)` deep-merges into
`attrs.profiles` (per-field; None removes; metric strings coerced to numbers). `add_or_edit_person`
gains a `profiles=` param; the dashboard `/people` POST threads `profiles` through. Storage is
generic (any field key accepted); the registry governs display, not storage.

## Out of scope / future
- Acquisition (scraping/SerpAPI) — separate work; pairs with FacultyFolio's opt-in model.
- A "most-cited faculty" structured skill over the metric attrs (one `json_extract` skill when wanted).
- Dashboard People-editor form fields for profiles (endpoint accepts them; UI inputs are a nicety).
- Registry-as-JSON endpoint to auto-generate the dashboard form (only if field count grows).

## Tests
`test_profile_fields.py` (registry render, alias-read, one-row extensibility), `test_structured_profiles.py`
(who-is→links, research→metrics, none for plain people / lists), `test_people_editor.py` (merge/coerce/
remove/coexist), `test_reconcile.py::test_reconcile_is_scoped_by_created_by`, `test_m3_departures.py`
(all-source drop on departure).

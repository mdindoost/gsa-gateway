# Scholar Interests → Research Areas (completing external-profiles bullet 3)

> **Status:** Design (COMPLETE version — Scholar areas first-class, both query directions). Scoping the
> UNBUILT third goal of `2026-06-19-person-external-profiles-design.md` (line 9: "Scholar research
> interests: fed into the EXISTING `ResearchArea` nodes + `researches` edges"). Awaiting the gate
> (senior-eng + RAG review — now incl. completeness-vs-plan — → Mohammad approves → TDD).
> **Date:** 2026-06-20 · **Author:** Claude (Opus 4.8) with Mohammad Dindoost.
> **Related:** `[[project_external_profiles]]`, `[[feedback_review_against_plan]]`,
> `[[feedback_no_bandaid_align_data_and_retrieval]]` (one uniform standard, verify on BOTH KB and KG),
> `v2/core/graph/project.py` (`area_key`, area upsert+reconcile), `v2/core/ingestion/{scholar,decompose,
> reconcile,people_editor}.py`, `v2/core/retrieval/{entity,skills}.py`.

## Problem (the gap the new completeness rule caught)
External-profiles listed three goals: links ✅, Scholar metrics ✅, **Scholar interests → research areas
❌ (never built, only quietly noted in a status line).** Effect: "Jamie Payton research field" deflects —
she has Scholar metrics (1,404 cit) but **0 research-area edges** (Deans' NJIT pages list no research and
we capture Scholar *numbers*, not *interests*). Same gap = the ~27 metrics-but-no-areas people with
dormant citations.

## Goal — Scholar areas become FIRST-CLASS, identical to crawler areas (Mohammad's uniform-standard rule)
Capture each enriched person's **Scholar interest tags** and merge them into the KG's research-area
representation (**union + dedup** with the crawler's "card" areas), producing the SAME artifacts the
crawler produces, so Scholar areas behave identically in **both** query directions:
- **Forward** — "X research field / X research" (`research_of_person`): lists the person's areas.
- **Reverse** — "who works on <area>" (`people_by_research_area`, FTS): finds the person by topic.
- Plus: their Scholar metrics un-dormant (metrics surface on `research_of_person` only when areas exist),
  and more faculty carry areas (feeds a future Find-Your-Advisor).

Self-asserted Scholar interests ARE legitimate research areas — not fabrication. Same NJIT-affiliation
confirmation as metrics guards wrong-person profiles.

## Design

### 1. Parse interests — `scholar.parse_scholar_interests(html) -> list[str]`
Scholar lists interests at `#gsc_prf_int a` (validated in the profiles-design finding). Add beside
`parse_scholar_metrics`. The MANUAL WebFetch path (owner's chosen method — Scholar blocks bots) already
returns interests; the on-demand `scholar.py` refresh gains interest capture too.

### 2. Write — `people_editor.set_person_research_areas(conn, person_key, areas, *, source="scholar")`
Produces the SAME artifacts the crawler does for a person's areas, source-tagged `'scholar'` so it never
collides with crawler data:
- **Graph:** for each area → `area_key()` → `upsert_node(type="ResearchArea", key, name, source="scholar")`
  (reuses the EXISTING node if the area already exists = union/dedup), then `upsert_edge(researches,
  area_source="external", source="scholar")`. Source-scoped reconcile deactivates this person's
  `source='scholar'` `researches` edges not in the new set (a refresh updates cleanly); crawler edges
  (`source='crawler'`) are untouched.
- **KB (the reverse-direction enabler):** upsert ONE `research_areas` `knowledge_item` for the person —
  `type='research_areas'`, `content` = "Research areas of <name>: a; b; c", `metadata.entity_id` = person
  key, `metadata.areas` = the tag list, **`created_by='scholar'`**, `source='dashboard'`. Idempotent /
  source-scoped: reconcile only this person's `created_by='scholar'` research_areas item, so the crawler's
  own research_areas item (`created_by='crawler'`) is never touched. The FTS5 trigger indexes
  `search_text` on insert → the person is immediately matchable in `people_by_research_area`. Returns the
  item id needing embedding.
- Does NOT commit (caller owns the txn), consistent with `set_person_profiles`.

### 3. Serve — TRUE union across ALL sources (the "OR + dedup" you described)
`research_of_person` today reads ONE `research_areas` KB item via `fetchone()` and only falls back to
edges if absent — so it would miss a second (scholar) item and miss edges. **Change it to return the
UNION of: every active `research_areas` KB item's `metadata.areas` (crawler + scholar) ∪ every active
`researches` edge's area — deduped by `area_key`, display-normalized.** This makes the merge real for
everyone (area-less people AND people who already have crawler areas). Statement handling unchanged.

`people_by_research_area` needs no change: it already FTS-matches `research_areas` KB items by
`entity_id` and returns DISTINCT people, so the new `created_by='scholar'` item makes Scholar-only people
matchable by topic automatically.

### 4. Embed + backfill
- New/updated scholar `research_areas` items are embedded by the existing `v2/scripts/embed_all.py`
  (resumable; embeds items missing a vector) — so they also join the semantic retriever, not just FTS.
- **Backfill** the ~49 people who already have Scholar metrics but never had interests captured: per
  person, fetch interests (manual WebFetch, same as metrics) → `set_person_research_areas` → `embed_all`.
  Gated (`hardened_backup` + commit), source-tagged `'scholar'`. Includes Jamie Payton.

## Open decisions (RESOLVED 2026-06-20, Mohammad)
- **D1 — `research_of_person` union (all KB items ∪ edges):** YES (the core "OR + dedup").
- **D2 — Scholar areas matchable in "who works on X":** **INCLUDED** (not deferred) — via the
  `created_by='scholar'` `research_areas` KB item, so Scholar areas are first-class in both directions
  (Mohammad's "one uniform standard / verify on BOTH KB and KG").
- **D3 — acquisition = manual WebFetch + one-time backfill:** YES (Scholar blocks bots).

## Goals checklist (completeness — per `[[feedback_review_against_plan]]`)
- [ ] G1 `scholar.parse_scholar_interests`.
- [ ] G2 `set_person_research_areas` — node + `researches` edge (source='scholar') **+** `research_areas`
      KB item (created_by='scholar'); union via `area_key`; source-scoped reconcile for BOTH edges and the KB item.
- [ ] G3 `research_of_person` returns the UNION of all research_areas KB items ∪ edges, deduped (D1).
- [ ] G4 Verify `people_by_research_area` surfaces Scholar-only people by topic (D2) — confirm, no code change expected.
- [ ] G5 Embed the new items (`embed_all`) + backfill existing Scholar people (incl. Jamie Payton).

## Testing (TDD)
- `scholar.parse_scholar_interests`: extracts tags from sample HTML; [] when none.
- `set_person_research_areas`: creates ResearchArea node + edge (source='scholar', area_source='external')
  **and** a `research_areas` KB item (created_by='scholar', metadata.areas/entity_id set, FTS-indexed);
  **reuses the existing area node** incl. case-fold ("Machine Learning"=="machine learning"); a second
  call updates (source-scoped reconcile drops removed scholar areas) and does NOT touch a `source='crawler'`
  edge / `created_by='crawler'` item for the same person.
- `research_of_person` union: scholar-only person → areas shown (Payton); person with crawler KB item +
  extra scholar areas → UNION, deduped; statement path unchanged.
- `people_by_research_area`: a scholar-only person is returned for a query matching a Scholar interest
  (reverse direction); DISTINCT (no dup when both crawler + scholar items match).
- Real-DB verify: "Jamie Payton research field" → her Scholar interests; "who works on computing
  education" → Payton appears; "Payton research" → metrics now also surface.

## Files touched
- `v2/core/ingestion/scholar.py` — `parse_scholar_interests`.
- `v2/core/ingestion/people_editor.py` — `set_person_research_areas` (graph + KB item, source-scoped).
- `v2/core/retrieval/entity.py` — `research_of_person` union (all KB items ∪ edges).
- `v2/tests/` — new tests; `eval/questions.txt` — forward + reverse questions for a Scholar-only person.
- Backfill = gated data run + `embed_all` (not code).

No schema change. The backfill is data (no restart); the `research_of_person` code change needs a restart.
After backfill, run `embed_all` so the new research_areas items join semantic retrieval.

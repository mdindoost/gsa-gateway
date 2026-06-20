# Scholar Interests → Research Areas (completing external-profiles bullet 3)

> **Status:** Design — COMPLETE version, REVISED per senior-eng + RAG review (2026-06-20, both incl. the
> completeness-vs-plan check). Mohammad approved the amendments → build via TDD next. Scoping the unbuilt
> 3rd goal of `2026-06-19-person-external-profiles-design.md` (line 9).
> **Date:** 2026-06-20 · **Author:** Claude (Opus 4.8) with Mohammad Dindoost.
> **Related:** `[[project_external_profiles]]`, `[[feedback_review_against_plan]]`,
> `[[feedback_no_bandaid_align_data_and_retrieval]]`, `[[feedback_no_manual_ops]]`,
> `v2/core/graph/project.py` (`area_key`), `v2/core/ingestion/{scholar,decompose,reconcile,people_editor}.py`,
> `v2/core/retrieval/{entity,skills}.py`, `skills._canonical`.

## Problem
External-profiles shipped links ✅ + Scholar metrics ✅ but **never built bullet 3 (Scholar interests →
research areas)** — only quietly noted in a status line (the gap the new completeness rule caught). So
"Jamie Payton research field" deflects (she has 1,404 citations but **0 research-area items**), and **48**
people have Scholar metrics but no areas → their citations stay dormant.

## Goal — Scholar areas FIRST-CLASS, identical to crawler areas, both query directions
Capture each enriched person's Scholar interest tags and merge them into the KG (**union + dedup** with
crawler "card" areas), producing the SAME artifacts the crawler does, so Scholar areas work in BOTH:
- **Forward** "X research field / X research" (`research_of_person`) and
- **Reverse** "who works on X" (`people_by_research_area`, FTS) — incl. **org-scoped** variants.

Plus: un-dormant their metrics (metrics surface on `research_of_person` only when areas exist). Self-
asserted Scholar interests are legitimate research areas (not fabrication); NJIT-affiliation confirmed at
capture guards wrong-person profiles.

## Design

### 1. Parse — `scholar.parse_scholar_interests(html) -> list[str]`
Tags at `#gsc_prf_int a`. Trim, drop empties, de-dup (mirror `parse_scholar_metrics`' guarding). `[]` when
none. **S6 (no-manual-ops):** wire it into the on-demand `scholar.refresh_scholar` too — when interests are
parsed, call `set_person_research_areas` — so the one sanctioned-provider swap later covers interests, not
just metrics. (Acquisition today stays manual WebFetch; this just makes the refresh path interest-aware.)

### 2. Write — `set_person_research_areas(conn, person_key, areas, *, org_id, source="scholar")`
Produces the SAME two artifacts the crawler does, source-tagged `'scholar'`:
- **`org_id` is REQUIRED (review B1)** — `knowledge_items.org_id` is NOT NULL and is what org-scoped
  reverse/facet queries filter on. Caller derives it from the person's **primary faculty `has_role` org**
  (the `category='faculty'`/`is_primary` edge); multi-role people (e.g. Payton admin@YWCC + faculty@CS) →
  the **faculty** org, co-filing with where they actually sit. If no faculty edge, use the single role's org.
- **Graph:** per area → `area_key()` → `upsert_node(ResearchArea, key, name, source="scholar")` (reuses the
  EXISTING node = union/dedup) → `upsert_edge(researches, area_source="external", source="scholar")`.
  Source-scoped reconcile of `source='scholar'` `researches` edges (deactivate this person's scholar edges
  not in the new set), mirroring `project_entity`'s `source='crawler'` scoping. Crawler edges untouched.
  - **S5 (accepted + documented, Mohammad):** the `(src,researches,dst)` index is UNIQUE, so if the crawler
    already linked an area Scholar also asserts, `upsert_edge` UPDATES the one edge and its `source` flips
    to 'scholar' (**last-writer-sets-source**). The edge stays active and resolves correctly both
    directions; only provenance flips and a re-crawl can't re-own that one edge. Accepted (no schema
    change). Test asserts: a shared area is NOT duplicated/deactivated and the person still researches it.
- **KB (reverse-direction enabler):** ONE `research_areas` `knowledge_item`:
  - `org_id` (above), `type='research_areas'`, `content="Research areas of <name>: a; b; c"` (never insert
    the generated `search_text` — invariant), `metadata.entity_id`=person key, `metadata.areas`=tag list,
    **`metadata.area_source='scholar'` (review S2)** so reverse/ranking consumers (incl. future FYA) can
    down-weight broad self-asserted tags, **`created_by='scholar'`**, `source='dashboard'`,
    **distinct natural_key `{key}:research_areas:scholar`** (review B2) so it never conflates with the
    crawler's `{key}:research_areas:main`.
  - **Idempotency = deactivate-then-insert (review B2)**, the `add_or_edit_person` pattern: `UPDATE
    knowledge_items SET is_active=0 WHERE json_extract(metadata,'$.entity_id')=? AND created_by='scholar'
    AND type='research_areas'`, then INSERT fresh. (No `reconcile_entity` — that's for full crawler
    decomposition.) Crawler's `created_by='crawler'` item is never touched. FTS auto-indexes on INSERT
    (search_text is generated + trigger). Returns the new item id for embedding.
- Does NOT commit (caller owns the txn).

### 3. Serve — TRUE union in `research_of_person` (the "OR + dedup")
Today it reads ONE `research_areas` item via `fetchone()` then falls back to edges → misses a 2nd item and
edges. **Change to: union of EVERY active `research_areas` item's `metadata.areas` (crawler + scholar) ∪
EVERY active `researches` edge area; group by `area_key`; emit `skills._canonical(forms)` per group
(review S1 — reuse the existing canonical picker, don't reinvent); deterministic sort (canonical casefold,
like `areas_in_org`) so ordering can't regress eval (review S2-order).** Plus **subsumption suppression
(review S1):** drop a Scholar-only area from the DISPLAY union when its casefold is a whole-token subset of
another area from a different source (e.g. "databases" ⊂ "Multimedia Databases") — keeps the edge/KB for
reverse recall but de-garbles the forward list. Statement handling unchanged.

`people_by_research_area` needs no code change (review G4 confirmed): it FTS-matches `research_areas` items
by `entity_id` and returns DISTINCT people (a person matched by both crawler+scholar items appears once) —
**contingent on B1's `org_id` being correct** for the org-scoped variant.

### 4. Embed + backfill
- `v2/scripts/embed_all.py` (resumable) embeds the new items → they join semantic retrieval too.
- **Backfill the 48** metrics-but-no-areas people (incl. Payton): per person, WebFetch interests (NJIT-
  affiliation confirmed, logged) → `set_person_research_areas(org_id=faculty-home)` → `embed_all`. Gated
  (`hardened_backup` + commit). DB-only → no restart for data.

## Decisions (RESOLVED 2026-06-20, Mohammad)
- D1 union (all KB ∪ edges): **YES.** D2 reverse first-class via the scholar KB item: **INCLUDED.**
- D3 acquisition manual WebFetch + backfill: **YES.** S5 shared-area edge last-writer-sets-source:
  **ACCEPT + document.** S6 wire `refresh_scholar` to capture interests: **YES.**

## Goals checklist (completeness — `[[feedback_review_against_plan]]`) — ALL SHIPPED 2026-06-20 (`2f1504b`)
- [x] G1 `parse_scholar_interests` (clean/dedup/empty) + wired into `refresh_scholar` (S6). Tests green.
- [x] G2 `set_person_research_areas(org_id required)` → node + `researches` edge (source='scholar',
      area_source='external') **+** `research_areas` KB item (created_by='scholar', distinct natural_key,
      metadata.area_source); deactivate-then-insert; source/created_by isolation; reuses area nodes WITHOUT
      renaming; S5 (shared-edge last-writer) documented. Tests green.
- [x] G3 `research_of_person` UNION (all KB items ∪ edges) → group by `area_key`, `canonical_area` display
      (relocated to graph.project, shared w/ skills), deterministic sort, subsumption suppression. Tests green.
- [x] G4 `people_by_research_area` un-scoped AND org-scoped returns scholar-only people, DISTINCT — verified
      live ("who works on computing education in computer science" → Jamie Payton).
- [x] G5 Backfill ran (`scripts/_backfill_scholar_interests.py --commit`): **75 metrics refreshed, 68 got
      research areas, 0 failed** (7 list no Scholar interests); `embed_all` embedded all 68; restarted.
      Verified: "Jamie Payton research field" → her areas + un-dormanted citations.

## Testing (TDD)
- `parse_scholar_interests`: tags from sample HTML; trimmed/de-duped; `[]` when none.
- `set_person_research_areas`: creates node + edge (source='scholar', area_source='external') **and** the
  KB item (created_by='scholar', metadata.area_source='scholar'/areas/entity_id, distinct natural_key,
  FTS-indexed, correct `org_id` from faculty edge); **reuses existing area node** incl. case-fold; second
  call **replaces, no dup** (deactivate-then-insert); a `source='crawler'` edge / `created_by='crawler'`
  item for the same person is NOT touched; **shared-area edge (S5): not duplicated/deactivated, person
  still researches it.**
- `research_of_person` union: scholar-only person → areas (Payton); crawler item + extra scholar tags →
  UNION, deduped, `_canonical` form, deterministic order, subsumption drops "databases" under "Multimedia
  Databases"; statement path unchanged. Re-run existing research-areas eval Q's → no order regression.
- `people_by_research_area`: scholar-only person returned for a matching topic **un-scoped AND org-scoped
  (org_id=faculty dept)** — DISTINCT.
- Real-DB verify: "Jamie Payton research field" → her interests; "who works on computing education in
  computer science" → Payton; "Payton research" → areas + citation suffix; "who works on machine learning"
  → still a sane bounded set (S2 over-return check).

## Files touched
- `v2/core/ingestion/scholar.py` — `parse_scholar_interests` + `refresh_scholar` interest wiring (S6).
- `v2/core/ingestion/people_editor.py` — `set_person_research_areas` (graph + KB item, org_id, deactivate-then-insert).
- `v2/core/retrieval/entity.py` — `research_of_person` union (`_canonical`, sort, subsumption).
- `v2/tests/` — new tests; `eval/questions.txt` — forward + scoped-reverse + broad-topic Q's.
- Backfill = gated data run + `embed_all` (not code).

No schema change. Backfill = data (no restart); the `research_of_person` change needs a restart; run
`embed_all` after backfill.

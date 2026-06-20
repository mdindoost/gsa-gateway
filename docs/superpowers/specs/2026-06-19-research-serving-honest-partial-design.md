# Research-Serving: Honest-Partial (anti-fabrication + metrics surfacing) — Design

**Date:** 2026-06-19  ·  **Status:** APPROVED 2026-06-19 (Part A only). Owner approved Part A; **Part B DROPPED**
(both reviewers + owner agreed surfacing citations on a "research" question is a category-shift and the seam
was wrong/double-printing; the 27 metrics-without-areas people keep their citations stored but dormant —
"not our fault" that areas are missing, no fabrication). Data check resolved the open question:
**case 2 is IMPOSSIBLE** (199 per-person research_areas items, 0 org-level) → collapse case 2 into case 3,
drop the route-flag plumbing. Researcher refinement folded in: compose clause also forbids ELABORATING a
listed attribute. Ready to build (Part A), diff back for sign-off before commit.

## Problem (two symptoms, one surface)
1. **Fabrication.** "What are the research areas of professors in the math department?" routes to `faculty_in_department` (a roster of NAMES); the 8B is asked for areas it wasn't given, so it **invents** a research area per professor. (Math actually HAS per-person area data for 21 of ~103 — it was never routed to.)
2. **Stranded metrics.** Of 48 people with Scholar metrics, **27 have no KG research areas**, so "X research" yields an empty `research_of_person` → RAG → their citations never surface. Their links still show on "who is X"; their impact numbers are stored but invisible.

Both live on the research/areas serving path. **Unifying principle: honest-partial — answer exactly what the data supports, explicitly state what's missing, never fabricate, and surface any real signal we do have (e.g. metrics).**

## Part A — Anti-fabrication (per senior + researcher review)
**New skill `faculty_areas_in_department(conn, org_id)`** (`skills.py`):
- Reuse `_area_rows(conn, org_id)` (`(area, entity_id)` over `metadata.areas`, already scoped via `org_descendants`), group by `entity_id` → `{eid: [areas]}` (case-fold dedup like `area_counts`), resolve names via the existing batch `_display_names`/`_named_rows` (no N+1), sort by name.
- **Enumerate ONLY people who have a `research_areas` item** (BLOCKER #1 — coverage is ~20% for math; do NOT left-join the full roster / do NOT emit "(no areas listed)" for ~80 people). Do not try to union graph-only faculty.

**Routing** (`router.py`) — BLOCKER #2 corrected placement: the fork goes in the **`_ENUM_AREAS` branch (~L226-233)**, specifically replacing the `if not _FACULTY_CUE` fall-through at ~L232. When `_ENUM_AREAS` matches, `_RANK` is false, and `_FACULTY_CUE` IS present, branch by data presence:
1. org subtree has per-person area items → `faculty_areas_in_department`
2. else org has only org-level area items → `areas_in_org` **with a "department-level, not per-professor" note** (rendered via a route flag/arg — `areas_in_org`'s existing no-faculty-cue output stays byte-for-byte unchanged)
3. else → `faculty_in_department` (names) **with an honest "I don't have their research areas" line**.
Presence check = one indexed COUNT over `research_areas` items scoped to `org_descendants` (same shape as `_area_rows`); the skill recomputes rows once (two small scans OK, no third).

**Rendering** (`structured_answer.py`): `faculty_areas_in_department` → "N of the {org} faculty list research areas: Name — a, b; Name — c; …" (state the count/scope honestly). Honest-partial wording for the (2)/(3) fallbacks; the deterministic text doubles as the offline fallback.

**Must NOT regress** (senior-verified routes): "who works on graph in CS" → `people_by_research_area`; "what research areas does CS cover" → plain `areas_in_org` (no note); "faculty in math" → `faculty_in_department`. New: "list faculty and their research areas in math" → `faculty_areas_in_department`.

## Part B — Metrics surfacing (the 27-gap)
Today `deterministic_suffix` appends Scholar metrics only when `research_of_person` STANDS (has areas or a statement). For people with metrics but no areas, the research answer is empty → no metrics.
**Change:** make `research_of_person` degrade to **honest-partial** when it has no areas/statement BUT the person has Scholar metrics: emit "I don't have specific research areas listed for {name}; their Google Scholar impact: 15,157 citations, h-index 68, i10-index 83 (as of 2026-06)." This is the SAME honest-partial principle as Part A — surface the real signal, state the gap, no fabrication. (Metrics stay OFF the identity card, per the owner's earlier "only when relevant" choice — research/impact questions only.)
- Mechanism: when `research_of_person` would be empty, check `attrs.profiles.scholar` metrics; if present, return an honest-partial structured answer carrying the metrics (so the structured path fires and renders deterministically). If neither areas nor metrics → empty → RAG (unchanged).

## Guardrail (cheapest reliable, per researcher)
- **No** answer-entity verifier (over-engineering for a local 8B).
- **Narrow** compose-prompt clause (`ollama_client.py` `compose_from_rows`): "Do not attach a research area, title, or other attribute to a name unless that exact attribute appears in the Facts for that name." Keep it purely prohibitive (let the deterministic Facts text own any "not available" disclaimer) and verify it doesn't make officer/`people_in_org` compose cases (legit null emails) start narrating "not available" noise.

## Explicitly out of scope
Answer-entity verifier; backfilling `researches` graph edges (math's areas live in `research_areas` items — separate data task); any RAG-path change; metrics on the identity card.

## Open question for the owner
For Part A's `areas_in_org`-with-note path (case 2), is the dept-level area list an acceptable answer to "areas of the *professors*", or should case 2 collapse into case 3 (names + honest "no per-person areas")? (Lean: keep case 2 — a real area list is more useful than a bare roster, as long as it's labeled department-level.)

## Eval anchors (per the grow-the-suite rule)
The headline bug query; the 4 no-regress adjacent queries above; one thin-coverage dept (math) exercising case-1-subset; one full-coverage dept (CS) exercising case 1; one metrics-without-areas person ("Zhi Wei research") exercising Part B.

## Files
`skills.py` (new skill + presence helper), `router.py` (fork at the `_ENUM_AREAS` branch), `structured_answer.py` (run + renderers + research_of_person honest-partial), `bot/services/ollama_client.py` (narrow compose clause), `eval/questions.txt`.

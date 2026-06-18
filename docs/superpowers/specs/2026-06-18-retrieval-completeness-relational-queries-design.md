# Retrieval Completeness + Relational/Entity Queries — Design Spec

**Date:** 2026-06-18
**Status:** Phase 1+2 IMPLEMENTED + tested (senior-reviewed GO-WITH-CHANGES; all MUST-FIX applied). Phase 3 (conversation follow-up focus) DEFERRED per review — and the meta-question handling ("why didn't you list him") CUT entirely (high-effort/rare; its failure mode is the exact hallucination we're preventing).

## Resolution (post-review)
Built in `v2/core/retrieval/entity.py` + router/structured_answer wiring. All senior-review MUST-FIX applied & verified against `gsa_gateway.db`:
- **No hallucinated roles.** `role_in_org` matches the title HEAD exactly ('dean' → Payton, NOT Associate Deans); absent role (no 'Chair' for Informatics) → `""` → falls through to RAG.
- **Single name source** = `nodes.name`, normalized `Last, First`→`First Last` everywhere.
- `research_of_person` prefers the clean `research_areas` tag doc over dirty `researches` edge strings; empty (e.g. Hai Phan) → RAG.
- `entity_card` EXCLUDES publication/webpage.
- `_try_structured` pre-gate widened to admit name/"tell me about"/"<name> research"/short messages.
- New person/role/research/card skills render `""` on empty → fall through to RAG (never dead-end).
- ROLE set trimmed (no president/vp/chief/provost); duties/process guard added.
- Verified live: dean-of-YWCC→Payton; "list all the Michaels"→complete (incl. Giorgio); "Guiling Wang's research"→his areas; "professor Wang"→5-way disambiguation; chair-of-Informatics & Hai-Phan-research→honest deflect. 24 new unit tests + full suite (556 passed, only the 7 documented pre-existing failures), no regressions to existing routes.

---

**Original status:** PROPOSED (for senior review before build)
**Tasks:** #1 (multi-doc-per-entity retrieval misses) + #2 (relational/enumeration + follow-up failures)
**Rule:** No band-aids. This addresses the documented ROOT problem (relational/entity queries served only by semantic top-K) with a coherent planning layer, not patches.

---

## 1. The failures (all reproduced)

| Class | Example query | Current result | Why it fails |
|---|---|---|---|
| Role lookup | "Who is the chair of Informatics?" | "no chair mentioned" | The chair fact is a `has_role` **edge** (category/title), not text. Router has no person↔role skill → semantic RAG → nothing to retrieve. |
| Entity facet | "Guiling Wang research?" | found profile, "research not in this doc" | Person is split across docs; the **profile** doc out-ranks the **research_areas** doc on the name query, so the answering facet never surfaces. |
| Name enumeration | "List all the Michaels" | incomplete + inconsistent | Top-K returns *most similar* few chunks, can't enumerate all matching people. No people-by-name path. |
| Follow-up (vague) | "more" / "more about professor wang" | drifted to grants / wrong Wang | Retriever uses the **current message only**; "more" has no entity, "professor wang" is ambiguous, no disambiguation. |
| Follow-up (meta) | "why didn't you list him" | hallucinated | Meta-question about the bot's own prior answer; retrieval matched literal words to noise. |

**Root cause:** the structured router handles only a narrow set of shapes (area→people, org→people, counts). Everything else falls to semantic top-K, which structurally **cannot enumerate**, **fragments entities across docs**, **can't see KG-only facts (edges)**, and is **context-blind on follow-ups** (history feeds generation, not retrieval).

## 2. Current architecture (grounded)

- `router.py` `route()` — rule-based; resolves an **org** (`_find_org`) and an **area**, maps to one of: `faculty_in_department`, `officers_in_org`, `people_in_org`, `people_by_research_area`, `count_people_by_research_area`, `areas_in_org`, `area_counts`, `people_by_area_tag`, `org_departments`. Returns `None` → semantic RAG.
- `skills.py` — the parameterized SQL. Helpers: `resolve_org`, `org_descendants`, `_display_names` (entity_id→name via profile/overview title), `_named_rows`.
- `structured_answer.py` `run()`/`format_answer()` — executes a `Route`, renders complete deterministic text (also the LLM grounding + offline fallback).
- `message_handler._try_structured()` — cheap pre-gate → `route()` → `structured_answer`. Runs BEFORE intent detection + RAG.
- `conversation.py` — per-user in-memory session, 5 turns, 60-min TTL. `INTENT_CLEAR_HISTORY` already exists.
- KG facts available: Person nodes (`nodes` type='Person', `key`=entity_id, `name`, `attrs.email`), `has_role` edges (category + `attrs.titles`, to an Org node bridging `attrs.org_id`), `researches` edges (Person→ResearchArea), `part_of`.

**Key invariant kept:** the structured/entity path produces a *complete, deterministic* answer; semantic RAG stays the catch-all for the open long tail. We only ADD precise paths; we never route a descriptive question into a skill (false-positive routing is the dangerous failure — router stays conservative).

---

## 3. Design — a query-understanding/planner layer

Four cooperating parts, built as three independently-testable phases.

### Phase 1 — Person entity resolution + relational skills (fixes role lookup, name enumeration, person→research)

**3.1 Person resolution (`skills.py`)**
```
resolve_people(conn, name_query) -> list[PersonHit]
   PersonHit = (entity_id, name, primary_org_name, primary_title)
```
- Match active Person nodes whose `name` contains ALL query tokens (case-insensitive, word-boundary), e.g. "michael" → every Michael; "guiling wang" → Guiling Wang; "wang" → all Wangs.
- Returns the COMPLETE set (powers both enumeration and disambiguation). `primary_org`/`primary_title` from the person's "best" `has_role` edge (faculty/admin/officer preferred) for display.
- Deterministic ordering by name.

**3.2 New skills**
```
people_by_name(conn, name) -> list[PersonHit]              # complete enumeration
role_in_org(conn, org_id, role_terms) -> list[(name,title,email)]
research_of_person(conn, entity_id) -> {areas:[...], statement:str|None, dept:str|None, name:str}
```
- `people_by_name` = `resolve_people` (rendered as a roster). Fixes "list all the Michaels" — complete, never top-K.
- `role_in_org`: people with a `has_role` to this org whose **title** (`attrs.titles`) or **category** matches any of `role_terms` (e.g. chair → {"chair"}; dean → {"dean"}). Word-boundary match on the title text. Fixes "who is the chair of Informatics" (Halper). Scoped to the org (not descendants — a chair is org-specific).
- `research_of_person`: the person's `researches` edges (area names) + their `research_areas`/`research_statement` doc text. Fixes "what does X research" deterministically when the KG/KB has it; honest empty when it doesn't (e.g. Hai Phan — no card).

**3.3 Router additions (`router.py`)** — conservative, only when shape is unambiguous:
- **role-in-org**: `(?:who(?:'s| is) )?(?:the )?<ROLE> of <org>` and `<org> <ROLE>` where ROLE ∈ {chair, co-chair, dean, associate dean, head, director, coordinator, president, vice president, chief, provost}. → `role_in_org`. (Officer titles in a GSA/club context already route to `officers_in_org`; ROLE here is academic/admin leadership. Guard against the existing officer/process branches.)
- **name enumeration**: `(?:list|name|show|all|every|any|are there|is there|do we have)` + a token that **resolves to ≥1 Person** → `people_by_name`. (We call `resolve_people` to confirm it's a real name before routing — no hard-coded names.)
- **person→research**: `<person> research|research area|works on|studies` where `<person>` resolves to exactly one Person → `research_of_person`. If it resolves to >1 → disambiguation (Phase 2).

**3.4 `structured_answer.py`** — render each new skill; empty stated honestly. Disambiguation rendering (Phase 2) lists candidates.

**Phase 1 tests** (mapped to failures): chair-of-Informatics returns Halper; list-all-Michaels returns the complete set incl. Giorgio; Guiling-Wang-research returns his areas; Hai-Phan-research deflects honestly; no false-positive routing on descriptive questions ("why is there a chair", "what does the chair do").

### Phase 2 — Entity-centric retrieval (fixes multi-doc fragmentation + ambiguity)

**3.5 Entity card (`skills.py` / new `entity_card.py`)**
```
entity_card(conn, entity_id) -> str   # complete grounded context for one person
```
Assembles, from KG + KB, a single block: name; role(s) + org(s) (from `has_role` titles); research areas (from `researches` edges + `research_areas` doc); education; bio/about; email. This is handed to the LLM as the grounding context (and is the offline fallback), so "Guiling Wang research" / "tell me about X" / "X's email" always see the *full* person, never one stray facet.

**3.6 Router: named-entity intent**
- "who is <person>", "tell me about <person>", "<person>'s email/office/title", or a bare "<person name>" that resolves to exactly **one** Person → entity-card path.
- Resolves to **multiple** (e.g. "professor wang") and not an enumeration → **disambiguation**: "There are 2 people named Wang: Guiling Wang (CS), Jason Wang (CS). Which one?" — never silently pick.

**Phase 2 tests:** Guiling-Wang-research via card returns areas even though the profile doc ranks highest; "professor wang" → disambiguation listing both; "<person> email" returns the email; entity card assembles all facets.

### Phase 3 — Conversation entity-focus (fixes follow-ups) — deterministic, no weak-LLM orchestration

The local 8B is unreliable at orchestration (per `router.py`), so follow-up resolution is **rule + state based**, not an LLM rewrite.

**3.7 Session focus (`conversation.py`)**
- After any answer about specific entities (structured skill or entity card), store `focus_entities: list[entity_id]` and `focus_org` on the session.
- A query-understanding step (in `_try_structured` or a new `_resolve_followup`) detects:
  - **vague continuation** ("more", "tell me more", "what else", "continue") → re-run the entity card / last skill for the focus entity with *expanded* detail.
  - **partial name** ("professor wang") that matches a focus entity → resolve to that focus entity (disambiguates "wang" → Guiling because Guiling is in focus). If it matches a focus entity, use it; else fall to normal disambiguation.
  - **pronoun reference** ("his research", "her email", "him") → bind to the single focus entity if exactly one.
  - **meta about prior answer** ("why didn't you list him/them", "you missed X") → re-run the LAST enumeration **completely** and check membership; answer factually ("X is/ isn't in the list because …"), never hallucinate.

**Phase 3 tests:** "Guiling Wang" then "more" → more about Guiling (not grants); "Guiling Wang research" then "what about his teaching" → Guiling's teaching; after "list the Michaels", "why isn't Giorgio there" → re-checks and answers correctly; topic switch (no focus match) still works (focus is only used when the message is clearly a follow-up).

---

## 4. Integration & ordering

`_try_structured` becomes: resolve follow-up focus (Phase 3) → `route()` (now incl. role/name/person + entity-card) → execute → render. Order of router branches: existing area/officer/people branches FIRST (unchanged behavior), then the new role-in-org, then name-enumeration, then person→research, then entity-card (most general person intent LAST). Entity-card only fires when a name resolves and no more-specific skill matched — so it never hijacks "who works on X".

## 5. Risks / decisions for review
- **R1 — False-positive routing.** Adding person/name routes risks pulling descriptive questions into skills. Mitigation: name routes require `resolve_people` to confirm a real Person; role routes require the ROLE-of-ORG shape; entity-card is the last, most-guarded branch. Need review of the trigger regexes.
- **R2 — Name collisions / common surnames.** "wang" matches many. Enumeration returns all (good); single-entity intents disambiguate (good). Confirm the disambiguation UX.
- **R3 — `role_in_org` title matching.** Titles live in `attrs.titles` free-text. "chair" must match "Chair", "Associate Chair", "Department Chair" but the design must decide whether "Associate Chair" answers "who is the chair" (probably list both, label roles). Review.
- **R4 — Entity-card cost.** Assembling a card is a few small queries — cheap. It replaces the semantic call for named-entity asks. Confirm it's used only when the entity resolves.
- **R5 — Phase 3 focus staleness.** Focus must clear/replace on a clear topic switch so it never injects a stale entity. Use it ONLY for messages classified as follow-ups (vague/pronoun/partial). Review the follow-up classifier.
- **R6 — No regression.** All existing structured tests + the eval harness must stay green; the new routes must not change answers to existing routed questions.

## 6. Build order (decomposed, each independently testable, gated)
1. Phase 1: `resolve_people` + `people_by_name` + `role_in_org` + `research_of_person` + router + structured_answer + tests.
2. Phase 2: `entity_card` + entity-card routing + disambiguation + tests.
3. Phase 3: session focus + follow-up resolver + tests.
4. Run the full failing-case suite + `scripts/eval.sh` + full pytest. Embed not needed (no new KB docs). No bot restart needed for DB-only? — code changes need restart; note it.

# Structured Retrieval — Phase 1 Design Spec

**Goal:** Fix the question shapes pure semantic RAG fails on — **enumerate / filter /
traverse / count** — by adding a *structured* retrieval path alongside the existing
semantic one, routed deterministically, with the LLM only **composing prose from real
rows**. Phase 1 kills the two proven failures: "who works on graph in YWCC" (returned
2 of 27, inconsistent) and "which departments are in YWCC" (couldn't answer).

**Architecture** (locked + twice-validated; see memory `project_retrieval_architecture`):
parameterized query **templates** + **deterministic-first router** + **constrained
slot extraction**; NO free-form text-to-SQL, NO GraphRAG, NO agent loops. Semantic RAG
is unchanged and is the safe default for everything not clearly structured.

---

## 1. Data model (verified against the live DB)

- A **person** = a distinct `json_extract(metadata,'$.entity_id')` (e.g.
  `people.njit.edu/profile/oria`). Active faculty: **CS(org 5)=57, DS(6)=25,
  Informatics(7)=1.**
- **Display name** = the person's `type='profile'` item `title` (e.g. "Vincent Oria");
  fallback to the `overview` title minus " — Overview", else the slug.
- **Research signal** lives in `type IN ('research_areas','research_statement',
  'overview')` content (37 faculty have explicit `research_areas`; overview covers the
  rest). Substring/keyword match is the Phase-1 mechanism (deterministic + complete);
  embedding-based synonym matching is a later enhancement.
- **Org tree** = `organizations(id,parent_id,name,slug)`: NJIT(1) › YWCC(4) › {CS(5),
  DS(6), Informatics(7)}; GSA(2) › PhD Club(8); MMI(3). "In YWCC" = the org's
  descendant set.

## 2. The skills (parameterized query templates) — new `v2/core/retrieval/skills.py`

Pure, deterministic Python functions over the DB. The LLM **never** writes these; it
only supplies validated slots. Each returns structured rows (never prose).

| Skill | Signature | Query (over existing tables) |
|---|---|---|
| resolve_org | `resolve_org(name) -> org_id\|None` | alias map ("YWCC"/"Ying Wu College"→4, "CS"/"computer science"→5, …) + `organizations.name/slug` match |
| org_descendants | `org_descendants(org_id) -> set[int]` | recursive walk of `organizations.parent_id` (shallow tree) |
| org_departments | `org_departments(org_id) -> list[str]` | `SELECT name FROM organizations WHERE parent_id=? AND is_active=1` |
| faculty_in_department | `faculty_in_department(org_id) -> list[(name,entity_id)]` | distinct `entity_id` in `org_id`, name from the profile item |
| people_by_research_area | `people_by_research_area(area, org_id=None) -> list[(name,entity_id)]` | distinct `entity_id` whose `research_areas/research_statement/overview` content matches `area` (case-insensitive), scoped to `org_descendants(org_id)` if given |
| count_people_by_research_area | `count_people_by_research_area(area, org_id=None) -> int` | `COUNT(DISTINCT entity_id)` of the above |

Person enumeration helper: `_people(org_ids, where_clause, params)` returns
`(entity_id → display_name)` so all skills share one correct "who is a faculty member +
their name" definition. Results are **complete and stable** (no top-K, no model
variance).

## 3. Router (Tier-1 deterministic) — `v2/core/retrieval/router.py`

`route(question) -> Route|None`. Returns a `Route(skill, slots)` only when the question
is **clearly structured**; otherwise `None` → existing semantic RAG (the safe default —
we never route a "describe X" question to a skill).

- **Trigger detection (rules):** enumerative/relational cues — `who|which|list|all|
  how many|count|name (some|the)` + structure cues (a department/college name, "research
  area"/"work(s) on", "departments in/of", "faculty in"). Descriptive cues ("tell me
  about", "what is", "explain", "who is <single name>") → `None`.
- **Skill selection (rules):** "departments in/of X" → `org_departments`; "faculty in
  X" / "list … faculty" → `faculty_in_department`; "who works on/research(es) X" /
  "researchers in X" → `people_by_research_area`; "how many … (work on|research) X" →
  `count_people_by_research_area`.
- Conservative by design: ambiguous → `None` (semantic RAG). False *negatives* (a
  structured Q falls to RAG) are tolerable; false *positives* (a descriptive Q forced
  into a skill) are not.

## 4. Slot extraction (constrained)

- **org/department slot:** resolved **deterministically** — scan the question for any
  org alias/name and `resolve_org`. No LLM needed; robust.
- **`area` slot (free text, e.g. "graph", "machine learning"):** a single
  **JSON-schema-constrained** LLM call (Ollama `format=json` / GBNF) returning
  `{"area": "..."}` extracted+normalized from the question. Validated: non-empty, sane
  length. (Fallback heuristic: the noun phrase after "work(s) on"/"research(es) in"/
  "on".) `llama3.1:8b` first; switch *only this step* to `qwen3:8b` if eval shows it's
  unreliable.
- **Validation gate:** if a required slot doesn't resolve (org not found, area empty)
  → return `None` → semantic RAG. A mis-extraction degrades to RAG, never a wrong query.

## 5. Composition

On a matched route: run the skill → get rows → the LLM **composes a natural-language
answer from those rows only** (its strength: prose; not correctness/recall). Reuse the
existing generation path but feed the structured rows as the grounded context (with a
prompt: "answer using ONLY these results; this is the complete list"). Empty result →
a truthful "no faculty match / unknown department" answer, not a hallucinated guess.

## 6. Integration

In the assistant/answer flow (`bot/core/message_handler.py` / `build_assistant`): for a
`question` intent, **try `route()` first**; if it returns a `Route`, run skill +
compose; else fall through to the **unchanged** semantic RAG pipeline. No change to
greetings/thanks/etc. or to the RAG path itself — purely additive.

## 7. Eval (built now, not last)

A seed labeled set (~30–50 Qs) in `bot/tests/data/` covering the failures + variants:
- routing labels (structured-skill-X vs semantic) → measure **router precision/recall**;
- for skill questions, the expected entity set / count / department list → measure
  **template correctness** (deterministic, exact).
A small runner reports per-skill + routing accuracy. This is the Phase-1 slice of the
golden-eval harness and the regression gate for every later phase.

## 8. Testing (TDD)

- **Skills** (pure SQL, deterministic): `org_departments(YWCC)` == {CS,DS,Informatics};
  `people_by_research_area("graph", YWCC)` returns the full set (not 2); count matches;
  `faculty_in_department(CS)` count == distinct CS entities; `resolve_org` aliases.
  Tested against a small fixture DB (a few orgs + entities + research items).
- **Router** (rules): structured questions → correct skill+slots; descriptive questions
  → `None`; ambiguous → `None`.
- **Slot validation:** unresolved org / empty area → `None`.
- **Integration:** a structured question hits the skill path; a descriptive one still
  hits RAG (RAG path untouched) — assert with the skill monkeypatched.
- LLM slot call is mocked in tests (no Ollama dependency in CI); live-verified
  separately.

## 9. Scope / non-goals

**Phase 1:** the 4 skills + helpers, the deterministic router, constrained `area`
slot extraction, composition-from-rows, integration, the seed eval set.
**Out (later phases):** `semantic-router` Tier-2 (fuzzy routing); events/contacts
skills (P2); embedding-based area synonyms; Vanna read-only free-form fallback (P5);
qwen3 swap unless eval forces it.

## 10. Risks

- **Substring area matching misses synonyms** ("graph" won't catch "network analysis").
  Acceptable for P1 (still vastly better than 2-of-27 and fully consistent); embedding
  synonyms are a later, measured enhancement.
- **Router false-positives** (descriptive Q → skill) are the dangerous failure →
  conservative rules + "ambiguous = semantic RAG"; the eval set measures this directly.
- **Slot extraction on 8B** → constrained JSON + validation gate (mis-extract → RAG),
  qwen3 swap available.
- **Composition hallucination** → prompt restricts to the returned rows; empty → honest
  "none found."

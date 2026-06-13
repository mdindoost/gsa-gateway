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
| resolve_org | `resolve_org(name) -> org_id\|None` | alias map ("YWCC"/"Ying Wu College"→4, "CS"/"computer science"→5, …) seeded from `organizations.name/slug` + hand aliases, case-insensitive |
| org_descendants | `org_descendants(org_id) -> set[int]` | recursive walk of `organizations.parent_id`, **including the root** (so "in YWCC" = `{4,5,6,7}` and catches people attached directly to node 4, e.g. the Dean) |
| org_departments | `org_departments(org_id) -> list[str]` | `SELECT name FROM organizations WHERE parent_id=? AND is_active=1` |
| faculty_in_department | `faculty_in_department(org_id) -> list[(name,entity_id)]` | distinct `entity_id` in `org_id`, name from the profile item |
| people_by_research_area | `people_by_research_area(area, org_id=None) -> list[(name,entity_id)]` | **FTS5 word-boundary match**, not substring (see below), scoped to `org_descendants(org_id)` |
| count_people_by_research_area | `count_people_by_research_area(area, org_id=None) -> int` | `COUNT(DISTINCT entity_id)` over the **same** FTS query as `people_by_research_area` (shared helper — list and count can never disagree) |

**Research-area matching MUST use FTS5 `MATCH`, not substring (B1 — verified).** A naive
`LIKE '%graph%'` returns **12** people for "graph" in YWCC, half wrong (graphics,
cryptography, geographic). The existing `knowledge_fts` virtual table (unicode61
tokenizer, `v2/core/database/schema.py`) with `search_text MATCH 'graph'` returns
**exactly 6** — the real graph researchers, zero false positives. The skill query:
```sql
SELECT DISTINCT json_extract(k.metadata,'$.entity_id')
FROM knowledge_fts f JOIN knowledge_items k ON k.id = f.rowid
WHERE f.search_text MATCH ?                 -- area term; phrases double-quoted
  AND k.is_active = 1                        -- B2: FTS indexes 334 INACTIVE versions too
  AND k.org_id IN (<descendants>)
  AND k.type IN ('research_areas','research_statement','overview');
```
Sanitize the area for FTS5 operators (`- * : " OR NEAR`) — wrap the term in double
quotes and escape embedded quotes — or a stray char throws a syntax error.

**`is_active=1` is non-optional (B2 — verified).** `knowledge_fts` is external-content
over ALL `knowledge_items` incl. 334 inactive prior versions; without the filter the
graph count is 7, not 6. The shared `_people(org_ids, where, params)` helper **always**
adds `k.is_active=1` so no skill can omit it; it returns `(entity_id → display_name)`
(name from the `profile` title; 100% coverage on live data, fallback chain kept as cheap
defense). Results are **complete and stable** — no top-K, no model variance.

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
- **`area` slot (free text):** **deterministic heuristic is the PRIMARY path (S3)** —
  the noun phrase after "work(s) on" / "research(es) (in)" / "on" / "in"; areas on this
  data are short noun phrases, so this handles the vast majority with zero model risk.
  Only if the heuristic yields nothing, fall back to **one JSON-schema-constrained LLM
  call** (Ollama `format=json`; this method doesn't exist on `OllamaClient` yet — add a
  thin one) returning `{"area":"..."}`. `llama3.1:8b` first; `qwen3:8b` for this step
  only if eval demands.
- **Validation gate:** if a required slot doesn't resolve (org not found, area empty/
  over-long) → return `None` → semantic RAG. A mis-extraction degrades to RAG, never a
  wrong query.

## 5. Composition

On a matched route: run the skill → get rows → the LLM **composes a natural-language
answer from those rows only**. **Add a dedicated `compose_from_rows(question, rows)`
(S1) — do NOT reuse `generate_answer`:** its prompt hard-codes RAG framing ("answer
using ONLY the documents… cite which document") and `num_predict=512`, which would
truncate a 57-name roster mid-list (re-creating the incompleteness we're fixing). The
new method uses a roster prompt ("Here is the complete list of N faculty… present all
of them; if the list is empty, say none were found") and a larger `num_predict`. Empty
result → a truthful "no faculty match / unknown department," never a guess.

## 6. Integration — hook BEFORE intent detection (B3.2 — verified)

The router hooks at the **top of `bot/core/message_handler.py` `handle()`, before
intent detection** — NOT gated on `INTENT_QUESTION`. Verified: structured questions are
mis-classified by the current intent layer — "list all CS faculty" → `statement`, and
"who works on **social network** analysis" → `social` (FOOD/SOCIAL keyword substring
trap) — so a question-gated hook would miss them. Flow: `route(clean_text)` → if a
`Route`, run skill + `compose_from_rows` + return; else fall through to the **exact
existing** intent→RAG code, untouched. Non-routed messages take the identical path they
do today, so greetings/food/RAG are 100% unchanged.

**DB access (S2):** skills need a connection. Follow `v2/integration/retriever_shim.py`
— open a fresh `get_connection(db_path)` per call inside `asyncio.to_thread` (WAL +
`busy_timeout`; FTS+plain SQL only, no sqlite-vec needed). `build_assistant`
(`assistant.py`) already knows the db path — pass it to the router/skills.

## 7. Eval (built now, not last)

A seed labeled set (~30–50 Qs) in `bot/tests/data/` covering the failures + variants:
- routing labels (structured-skill-X vs semantic) → measure **router precision/recall**;
- for skill questions, the **corrected** expected set/count/list (S4): "graph in YWCC"
  → exactly the **6** FTS researchers, and **negative labels** — graphics/cryptography/
  geographic faculty must **NOT** appear (these catch a regression back to substring);
  encode the **YWCC-root** count decision (`{4,5,6,7}`) explicitly.
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

- **FTS is token-exact, so it misses synonyms/morphology** ("graph" won't catch
  "network analysis" or "spectral methods"). This is the right P1 tradeoff — exact +
  complete + zero false-positives beats 2-of-27 — and embedding-based synonym expansion
  is a later, measured enhancement (not free-form substring, which we rejected for its
  false positives).
- **Router false-positives** (descriptive Q → skill) are the dangerous failure →
  conservative rules + "ambiguous = semantic RAG"; the eval set measures this directly.
- **Slot extraction on 8B** → constrained JSON + validation gate (mis-extract → RAG),
  qwen3 swap available.
- **Composition hallucination** → prompt restricts to the returned rows; empty → honest
  "none found."

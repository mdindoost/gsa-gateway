# A15 — topic→people routing (validated loose-area) design

**Date:** 2026-07-04
**Status:** DRAFT → Fable design-review → build TDD → Fable diff → ship. Owner approved the DIRECTION.
**Split of:** the accuracy roadmap (`project_pipeline_accuracy_review`), A15 — root cause of 3/4 of the
graph/neuroscience bad answers the owner flagged.

## Problem (execution-proven via the fixed ask.sh, 2026-07-04)
Natural topic→people phrasings fall to RAG instead of the deterministic `people_by_research_area` skill,
where RAG stitches together wrong-topic people. Routing flow: `UnifiedRouter.decide()` → **fast_path**
(fires on `_FASTPATH_CUE` incl. faculty/professors/who → runs `router.route()`) → **classifier** →
slot-extractor. When `route()` returns None, the query reaches RAG (the classifier/slot-extractor rescues
SOME, inconsistently — it's LLM-driven).

Mapped 20 variants; **6 miss to RAG**, all natural:
| Query | route() | decide() | why |
|---|---|---|---|
| "faculty working neuroscience" | None | RAG | `_AREA_TRIGGER` needs "working **on**" |
| "faculty in neuroscience" | None | RAG | bare "in" not a trigger verb |
| "neuroscience faculty" | None | RAG | topic-first — no trigger |
| "professors who study neuroscience" | None | RAG | trigger has studies/studying, not bare "study" |
| "professors doing neuroscience" | None | RAG | "doing" not a trigger verb |
| "list faculty in machine learning" | None | RAG | enumerate + bare "in" |

Working today (unchanged, must stay): "who works on X", "researchers in X", "who studies X", "people who
work on X", "CS faculty who work on AI", "anyone working on X". The classifier rescues "which professors
research X" / "whose research is in X" (slot-extractor) — nondeterministic, not relied on.

## Root cause
`router.py:28 _AREA_TRIGGER` is too narrow (explicit verbs only) and there is **no topic-first pattern**
("neuroscience faculty") and **no bare-"in" pattern** ("faculty in neuroscience").

## Fix — a VALIDATED loose-area extraction in `route()`, tried only when the strict trigger misses
Add loose surface patterns, each gated on the candidate topic being a REAL research area.

### Surface patterns (run on the ORG-STRIPPED query `q_for_area`, so org queries are already peeled off)
1. **Topic-first:** the query is essentially `<topic> <people-noun>` — people-noun (`faculty|professors?|
   researchers?`) is the FINAL token, ≤4 total tokens, NO officer/role cue AND no `_RANK_CUE`/metric cue
   present (belt over the metric block — Fable nice-to-have #2). Candidate = tokens before the people-noun,
   **with leading determiners (the/a/an) STRIPPED** ("the neuroscience faculty" → "neuroscience"; note
   "the neuroscience" = 0 in FTS phrase-match, so NOT stripping would make the fix miss its own class —
   **Fable req #4**).
2. **Loose verb / bare-in:** `(faculty|professors?|researchers?|people|who|anyone)` … then
   `in|doing|study|studies|studying|focus(?:es|ing)?\s+(?:on|in)|work(?:s|ing)?\s+in` … `<topic>`.
   e.g. "faculty in neuroscience", "professors doing neuroscience", "professors who study neuroscience".
   **Do NOT strip determiners here** — "faculty in the news" must stay "the news" (=0 → RAG); bare "news"
   validates even tags-only (Brook Wu, "fake news detection"), so stripping would break the precision-neg
   (**Fable req #4**).

### The precision guard (the crux — Fable-hardened)
A loose candidate routes to `people_by_research_area` only when ALL hold, else return None (→ RAG):
- **(#1 tags-ONLY validation)** `_area_tag_people(conn, topic, org_id)` returns ≥1 — a NEW sibling of
  `_research_entities` that matches `type='research_areas'` ONLY (not the 3-type set incl.
  research_statement/overview), sharing `_fts_query`/`expand_area`. Measured on live DB: under all-3-types
  the prose lets adjective-modifiers validate ("visiting"=1, "graduate"=1, "office"=2); tags-only they're
  all 0 while every real target keeps matches (neuroscience=5, machine learning=36, ai=25). A topic is
  real iff someone LISTS it as an area. Tag matches ⊂ skill matches, so a validated topic still guarantees
  the skill (which uses the 3-type set) returns ≥1. (**Fable req #1**; use a `LIMIT 1` existence variant
  for the list route — nice-to-have.)
- **(#2 fuzzy-org guard)** `fuzzy_org(conn, candidate)` is EMPTY. The spec's "org-first always wins" claim
  is FALSE on real data: `_find_org("management faculty")` returns None (MTSM aliases lack bare
  "management"), yet "management" validates as an area (24 tag matches) → a confident WRONG-SCOPE answer.
  Same for "business/data/design faculty". If the candidate fuzzy-matches any org → NOT an area → None.
  Verified: all 6 targets → fuzzy_org=[] (nothing lost); management→[mtsm], data/design→[2], business→[1]
  (all correctly blocked). Do NOT auto-route a 1-hit fuzzy_org to faculty_in_department in A15 — follow-up
  ticket (needs alias-quality review; "business"→business-data-science not MTSM). (**Fable req #2**)
- **(#3 facet-word stoplist)** the normalized candidate is NOT exactly `research|researches|research
  area(s)|work` — else "professors doing research" → "research" validates with 307 people → a 307-name
  dump. (science/engineering/technology are already caught by #2.) (**Fable req #3**)

### Count intent
"how many faculty work in neuroscience" / "how many professors do neuroscience" — the loose path feeds
`count_people_by_research_area` (same validation) when a "how many"/"how much" cue is present.

### Placement + q_for_area hygiene (Fable req #5)
Insert the loose-area branch AFTER the `_ENUM_AREAS` block (`router.py:~575-585`, org-gated + more
specific) and BEFORE the metric block. New helper `_extract_area_loose(q_for_area) -> str | None` returns
the raw candidate (surface only, no DB); route() applies #1/#2/#3 then routes count vs list. Keep
`_extract_area` (strict) untouched. **q_for_area hygiene:** the C1 org-strip replaces the org phrase with
`" "`, leaving a dangling "in"/doubled spaces ("machine learning faculty in YWCC" →
"machine learning faculty in") — collapse whitespace + trim a trailing `in|at|of|within` BEFORE the loose
patterns, else scope-boundary (c) "org-scoped covered for free" is false.

## Scope boundaries
- **In:** deterministic `route()` loose-area + validation guard (list + count). This is what fast_path
  calls, so it fixes the whole faculty/professor/who/people/anyone surface deterministically.
- **Out (noted, NOT built):** (a) **A15b** — RAG still stitches wrong-topic people for genuinely-RAG
  queries (compose-verification gap, sibling of A4); the routing fix REMOVES the RAG path for valid-area
  queries, which is the main win. (b) LLM slot-extractor/classifier exemplar tuning for cue-less phrasings
  ("anyone into robotics") — edge, low value. (c) org-scoped loose-area ("neuroscience faculty in CS") —
  org resolution already sets org_id, so it's covered for free where org resolves; no special work.

## Invariants / safety
- Never fabricate: an unvalidated topic → RAG, never an empty/guessed KG answer.
- GSA-equal: no bias; topic queries answered from the KG for ALL NJIT areas.
- Deterministic + testable; no LLM in the loose path.
- Strict trigger + all existing branches unchanged → regression surface = the new loose branch only.
- One extra `count_people_by_research_area` query per loose-candidate query (cheap; only when strict
  missed AND a people-noun cue is present).

## Tests (TDD) — router-level, DB-backed (mirror v2/tests/test_router*.py; add to eval/questions.txt)
- Each of the 6 failing variants → `people_by_research_area` w/ the right `area`, given a seeded person who
  LISTS that area as a `research_areas` tag. Topic-first, bare-in, doing, bare-study, working-no-on, list-in.
- **Determiner split (#4):** "the neuroscience faculty" → area (topic-first strips "the"); "faculty in the
  news" → NOT area (bare-in keeps "the news"; seed a person whose STATEMENT says news but no tag → tags-only
  keeps it out too).
- **Precision negatives:** "senior faculty", "adjunct faculty", "visiting faculty", "graduate faculty" →
  NOT people_by_research_area (tags-only #1 rejects the modifier).
- **Fuzzy-org guard (#2):** "management faculty" / "faculty in business" → NOT area (fuzzy_org non-empty) →
  RAG. "CS faculty" → faculty_in_department (org-first, unchanged).
- **Facet stoplist (#3):** "professors doing research" → NOT area (candidate == "research") → RAG.
- **Metric collision (#6 — corrected):** "most cited faculty" → `top_people_by_metric` (loose topic-first
  excludes `_RANK_CUE`/metric cue AND "most cited" 0-validates); pin the real route.
- **Role collision (#6 — corrected):** "who is the neuroscience faculty coordinator" does NOT go None→RAG —
  the role branch fires `people_by_role("coordinator", …)` ("who is" satisfies `_PERSON_INTENT`). Assert
  "not people_by_research_area" and pin the ACTUAL route (not RAG).
- **Count:** "how many faculty work in neuroscience" → `count_people_by_research_area`.
- **Org-scoped (#5):** "machine learning faculty in YWCC" → area + org_id (after q_for_area hygiene);
  "neuroscience faculty in MTSM" with 0 in-scope → validated-scoped 0 → RAG (honest, no cross-scope).
- **No-regression:** "who works on X", "researchers in X", "who studies X", "CS faculty who work on AI"
  still route (strict path untouched).
- **Validation gate:** a topic with 0 matching people → None (→ RAG), seeded empty area.

## Fable diff-review hardening (R1/R2 — live-DB measured, folded in)
- **R1 tag-VALUE validation:** `is_listed_research_area` matches the candidate (+ `expand_area` synonyms)
  WORD-BOUNDARY inside a `metadata.areas` TAG VALUE (via `_area_rows`), NOT `search_text` — because
  search_text = title‖content includes the person NAME, so "wang/koutis faculty" (8/1 people) and tokens
  inside multi-word tags validated by name/incidental match. Word-in-tag keeps "neuroscience" (only inside
  "computational neuroscience") while killing names. ⊂-skill guarantee preserved (a matched tag lives on a
  research_areas item → the skill's 3-type FTS returns it).
- **R2 people-qualifier facet-stop:** `new/recent/current/former/international/retired` match word-boundary
  inside real tags ("new product development", "International Finance") so R1 can't catch them → the bare
  qualifier is stoplisted. Multi-word topics starting with them ("new product development faculty",
  "international relations faculty") are NOT stopped and still route.
- **ACCEPTED tradeoff (tags-only precision cost):** an area present ONLY in prose (research_statement/
  overview) but NEVER tagged (e.g. "cryptography" = 1 prose-only person on live) won't route via the LOOSE
  path → falls to RAG. It STILL routes via the STRICT `_AREA_TRIGGER` phrasing ("who works on cryptography"
  → 3-type FTS). Loose phrasings are KG-routed for TAGGED areas (the vast majority); this is the required
  cost of killing the name/adjective false-positives.

## Goals checklist (shipped/deferred)
- Loose-area surface patterns (topic-first + loose-verb/bare-in) — IN
- Validation guard via count_people_by_research_area — IN
- Count intent on the loose path — IN
- Precision negatives + org-first preserved + strict unchanged — IN (tests)
- A15b (RAG wrong-people stitching) — DEFERRED (separate, sibling of A4)
- slot-extractor/classifier exemplar tuning — DEFERRED (edge)

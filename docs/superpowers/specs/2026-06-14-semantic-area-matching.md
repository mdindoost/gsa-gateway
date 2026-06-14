# Area-Matching via Curated FTS Query-Expansion (Retrieval Phase 2) — Design Spec

**Status:** supersedes the earlier "semantic embedding-threshold" draft of this spec,
which the senior review and a direct measurement **disproved** on this corpus (see §0).

**Goal:** Make "who works on X" find faculty when X is an abbreviation or synonym of the
words actually in the profiles. Phase 1's `people_by_research_area` uses token-exact FTS,
so "llm" returns 0 even though faculty work on "large language models" / "generative AI" /
"natural language" (verified on the live DB: FTS `llm`=0, `large language`=1,
`generative`=3, `natural language`=3, `machine learning`=23).

---

## 0. Why not embeddings (the evidence that killed the previous draft)

The previous draft proposed a semantic KNN leg with a distance threshold. Measured on the
real corpus, **nomic-embed does not know the abbreviations**, so there is no separable
threshold:
- L2(embed_query("llm"), "large language models") = **0.977** (essentially unrelated),
  and "llm" is actually **closer** to "machine learning" (0.961).
- Distances are a smooth continuum: at the threshold where "llm" returns its faculty
  (~1.05), "basket weaving" → 23 hits and "machine learning" → 78 of 81. No cut separates
  signal from noise.
- vec0 KNN also needs its `LIMIT` applied before the join-filters, and the research-type
  vectors (155 of ~2957) sit at KNN ranks 600–1000, so a modest pool never reaches them.

**Conclusion (the principled one, driven by the data):** abbreviation/synonym resolution
here is a **vocabulary** problem, not a semantic-distance problem. The right tool is a
small, bounded, auditable **query-expansion map** feeding the existing FTS leg —
deterministic, precise, no embedder/vec/threshold, no connection change. This reverses our
earlier "embeddings principled / map patchy" prior: on this corpus the map is the correct
tool, not a patch.

## 1. The change — `_research_entities` expands the area into an FTS OR-query

Today `_research_entities` builds one FTS phrase from the raw area
(`_fts_term(area)` → `"large language models"`). Phase 2 expands the area through a curated
map into a **disjunction** of phrases, then runs the *same* FTS leg over the union:

- `expand_area("llm")` → `["llm", "large language model", "large language models", "LLM",
  "generative ai", "natural language"]` (illustrative).
- Build the FTS query as an OR of quoted phrases:
  `"\"llm\" OR \"large language model\" OR \"large language models\" OR …"`.
- Everything else in `_research_entities` is unchanged: `MATCH`, `is_active=1`,
  research-type filter, org-subtree scope, distinct entity_id.
- `people_by_research_area` and `count_people_by_research_area` already call
  `_research_entities`, so list and count stay in lockstep automatically.

An area **not** in the map falls back to the single-phrase behavior (exactly Phase 1) — so
this is purely additive and never regresses an exact term.

## 2. The expansion map (curated, bounded, org-agnostic)

A module-level dict in `skills.py` (or a sibling `area_synonyms.py`), keyed by the
**normalized** area token, value = list of expansion phrases:

```python
AREA_SYNONYMS = {
    "llm":  ["llm", "large language model", "large language models", "generative ai"],
    "llms": [... same as "llm" ...],
    "nlp":  ["nlp", "natural language processing", "natural language"],
    "ai":   ["ai", "artificial intelligence"],
    "ml":   ["ml", "machine learning"],
    "cv":   ["cv", "computer vision"],
    "hci":  ["hci", "human computer interaction", "human-computer interaction"],
    # … grown deliberately, each entry justified by terms that actually occur in the KB.
}
```

Design rules:
- **Org-agnostic** — these are field abbreviations, not NJIT-specific, so the same map
  serves every tenant (multi-tenant for free, per the locked architecture).
- **Symmetric where it matters** — the abbreviation maps to the full forms AND keeps the
  abbreviation itself (some profiles do write "LLM").
- **Bounded & justified** — every entry exists because the long form occurs in real KB
  text (we verify with FTS counts before adding), so the map can't silently over-match.
- **Normalization:** lowercase, strip, collapse internal whitespace, and singular/plural
  folded by storing both keys (`llm`/`llms`) — no stemmer dependency.

This is **not manual per-question patching**: it is one small reference table applied
uniformly by one mechanism (the same objection that made templates principled over
per-question rules). Growing it is a data-curation task, the entries are auditable, and the
fallback for an unknown term is well-defined.

## 3. Determinism & honesty (unchanged from Phase 1)

- Pure FTS over a fixed map → same question, same set, every run. Phase 1's
  "complete + stable" guarantee holds; no LLM/embedding variance enters.
- Empty result → the existing honest "I couldn't find anyone working on X"; never a guess.
  `compose_from_rows` still forbids inventing/expanding terms downstream.
- No connection change: the handler keeps the plain `sqlite3.connect` (FTS-only). No
  embedder, no vec0 load.

## 4. Eval (extend the Phase-1 seed set; this is the regression gate)

Add labeled cases and assert them against the live DB in the eval harness:
- **Recall:** "who works on llm in YWCC" → includes the large-language / generative / NLP
  faculty (the union of the expanded phrases), not the empty set; "nlp" → the
  natural-language faculty; "ai", "ml" likewise.
- **Precision guard:** an unrelated area ("basket weaving") → empty; a tight term ("graph")
  with no map entry → still exactly the graph set (Phase-1 behavior unchanged), NOT all of
  ML — proves expansion only fires for mapped terms.
- **Lockstep:** for every case, `count_people_by_research_area == len(people_by_research_area)`.

## 5. Testing (TDD)

- **Unit — `expand_area` (pure):** mapped token → expected phrase list (incl. plural key);
  unknown token → `[token]` (single-phrase fallback); normalization (case/whitespace).
- **Unit — `_research_entities` with a fixture DB:** insert known research_areas items +
  FTS rows for a few entities whose text says "large language models" / "generative ai";
  assert `people_by_research_area("llm")` returns them; assert an unmapped unrelated term
  returns empty; assert org-subtree scoping still filters; assert count == len(list).
- **Unit — precision:** a mapped term must not pull entities that only match an *unrelated*
  word (the OR is over the curated phrases only).
- **Live (manual, after restart):** real "who works on llm in YWCC" returns the
  large-language faculty; "graph" unchanged; "nlp"/"ai"/"ml" sane.

## 6. Scope / non-goals

**In:** `expand_area` + the curated `AREA_SYNONYMS` map; `_research_entities` building an
FTS OR-query from the expansion; eval cases (recall + precision guard + lockstep); TDD
units. No new deps, no connection change.

**Out:**
- **Embeddings / vec / distance threshold** — disproven on this corpus (§0); do not
  re-attempt.
- **The research-area facet (P2.5)** — turning the `research_areas` card into a clean
  discrete structured field. Note: where that card is *dirty* (run-on text), the fix
  belongs at the **crawl/extraction** step (store clean, pure data at the source), not a
  downstream normalizer — that is the enrichment/ingestion track, separate from this spec.
- **events/contacts skills (P3); data-coverage enrichment** (a professor whose profile
  never mentions an LLM-adjacent term in any words can't be found by any matcher — that's
  the source-data track, called out so expectations are right).

## 7. Risks

- **Map coverage** — only mapped abbreviations expand; unmapped synonyms still miss. Bounded
  and intentional: the map grows from observed KB vocabulary, each entry verified by FTS
  counts. Degrades gracefully to Phase-1 exact match, never to a wrong fact.
- **Over-expansion** — a too-broad expansion phrase could pull loosely related people. Guard:
  every phrase must correspond to real KB text and be reviewed; the precision-guard eval
  case is the regression gate.
- **Coverage still bounds it** — see §6 (source-data track).

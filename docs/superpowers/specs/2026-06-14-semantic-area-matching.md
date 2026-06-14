# Semantic Area-Matching (Retrieval Phase 2) — Design Spec

**Goal:** Make "who works on X" find faculty by **meaning**, not exact token. Phase 1's
`people_by_research_area` uses token-exact FTS, so "llm" returns 0 even though faculty
work on "large language models" / "generative AI" / "natural language" (verified: FTS
`llm`=0, `large language`=1, `generative`=3, `natural language`=3). This adds a
**semantic** match leg so abbreviations/synonyms resolve — the "semantic" half of the
hybrid architecture, applied inside the skill.

**Key property:** embeddings are **deterministic** (same query → same vectors → same
results), so this keeps Phase 1's complete-and-consistent guarantee — it's a similarity
*threshold* instead of exact tokens, not run-to-run LLM variance.

**Scope note:** this fixes the **matching** layer. The separate **data-coverage** limit
(NJIT profiles are thin on current topics; Google answers richer but over-includes —
e.g. lists departed faculty — so it's not ground truth) is the parked enrichment track,
not this spec.

---

## 1. The change — `people_by_research_area` becomes hybrid (FTS ∪ semantic)

Today it returns the FTS-exact set. Phase 2 returns **FTS-exact ∪ semantic-KNN**:
- **FTS leg (unchanged):** exact token matches — high precision (e.g. "security", "machine
  learning"). Keeps current behavior as a floor.
- **Semantic leg (new):** `Embedder.embed_query(area)` → 768-vec; KNN over
  `knowledge_vectors`; keep matches that are (a) `is_active=1`, (b) a research type
  (`research_areas`/`research_statement`/`overview`), (c) in the org subtree, and (d)
  **within a distance threshold**. The union of both legs' entity_ids is the result.
- `count_people_by_research_area` counts the same union (shared helper — list and count
  can't disagree).

This lifts recall (llm→large-language folks, graph→network-analysis) without per-term
synonym patches, while the FTS floor preserves exact-match precision.

## 2. Mechanism (follows the retriever's `_semantic` pattern)

```python
qvec = embedder.embed_query(area)                 # sync, normalized FLOAT[768]
rows = conn.execute(
    "SELECT item_id, distance FROM knowledge_vectors "
    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
    (sqlite_vec.serialize_float32(qvec), KNN_FETCH)).fetchall()
# then join knowledge_items and filter: is_active, research type, org-subtree,
# distance <= THRESHOLD → distinct entity_id
```
vec0 KNN takes `MATCH ? ORDER BY distance LIMIT N`; the type/org/is_active/threshold
filters are applied on the join (in SQL or Python), exactly as the retriever fetches a
pool then filters `allowed`. `KNN_FETCH` is a generous pool (e.g. 200) so the post-filter
has enough candidates; `THRESHOLD` is the real knob (below).

## 3. Connection + embedder wiring

- The semantic leg needs **sqlite-vec loaded**, so the structured path switches from the
  plain `sqlite3.connect` (Phase 1, FTS-only) to **`get_connection(db_path)`** (loads
  vec0; FTS5 still works on it). The handler's keyword pre-gate already limits this to
  structured-looking questions, so the vec-load cost is paid only then.
- The skill needs an **`Embedder`**. `embed_query` is synchronous (Ollama HTTP), so it
  runs fine inside the existing `asyncio.to_thread`. Pass the embedder from
  `build_assistant` (it already builds one for the retriever) into the structured path;
  if no embedder is available, the semantic leg is skipped (FTS-only fallback — never an
  error).

## 4. The threshold (the one real tuning knob)

L2-normalized vectors → vec0 `distance` ranks like cosine. We keep matches with
`distance <= THRESHOLD`. Too loose → false positives (people only loosely related); too
tight → misses synonyms. It is **tuned by the eval set (§6)**, not guessed, and is
**admin-configurable** via a `settings` row (`retriever.area_distance_max`, the same
pattern as the existing retriever boosts) so it can be adjusted without a deploy.
Default chosen from the eval, documented.

## 5. Determinism & honesty

- Embeddings are deterministic → same question, same set (no LLM variance). Phase 1's
  "complete + stable" holds.
- Empty union → the existing honest "I couldn't find anyone working on X" (never a
  guess). The downstream `compose_from_rows` already forbids inventing terms.

## 6. Eval (extend the Phase-1 seed set)

Add semantic cases to the labeled set and use them to pick `THRESHOLD`:
- "who works on llm in YWCC" → should include the large-language / generative / NLP
  faculty (Hai Phan, Guiling Wang, Jason Wang, …), not the empty set.
- Precision guard: an unrelated area (e.g. "basket weaving") → empty/near-empty; a
  tight-topic area ("graph") → still the graph set, not all of ML.
- Report recall + precision per threshold; pick the threshold that maximizes recall
  while keeping precision sane on these labels. This is the regression gate for the
  threshold.

## 7. Testing (TDD)

- **Unit (no Ollama):** fixture inserts known 768-vectors into `knowledge_vectors`
  (`sqlite_vec.serialize_float32`) for a few entities, with a **stub embedder** returning
  a chosen query vector; assert the semantic leg returns the near entities within
  threshold and excludes the far ones; assert FTS ∪ semantic union; assert count == len(list).
- **Unit:** no-embedder → FTS-only (semantic leg skipped, no error).
- **Live (manual):** the real "who works on llm in YWCC" returns the large-language
  faculty; "graph" still returns its set; threshold sanity on a few areas.

## 8. Scope / non-goals

**In:** the semantic leg + FTS union in `people_by_research_area`/`count_…`, the vec
connection + embedder wiring, the configurable distance threshold, eval cases to tune it.
**Out:** a curated synonym map (the embedding match supersedes it); applying semantic
matching to non-area skills (org/department/faculty are exact by nature); events/contacts
skills (P3); data enrichment (separate track).

## 9. Risks

- **Threshold tuning** is the crux — eval-driven, configurable, documented; conservative
  default. A bad threshold degrades gracefully (more/fewer names), never a wrong fact.
- **Semantic false positives** (e.g. "llm" pulling generic ML people) — bounded by the
  threshold + the FTS floor for exact terms; measured by the eval precision guard.
- **vec-load per structured query** — gated by the keyword pre-gate; small corpus, fast.
- **Coverage still bounds it** — if a professor's profile never mentions LLM-adjacent
  work in any words, no matcher can find them; that's the enrichment track, called out so
  expectations are right.

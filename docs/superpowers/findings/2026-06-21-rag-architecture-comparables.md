# RAG / Router architecture — comparable projects & ideas (research, 2026-06-21)

> **Status: research / decision pending.** Gathering analogous open-source projects to (a) get
> product ideas and (b) solve the **router-brittleness** problem (NL phrasings mis-route; e.g.
> "top 10 by citations in X" falls through to semantic search instead of the exact KG skill).
> Multiple LLMs (Claude, ChatGPT, others) are each being asked the same brief; their findings are
> collected here, then we compare and decide. **Any actual router change still goes through the
> expert-review HARD GATE.**
>
> All GitHub stars / last-push / license / archive figures below were pulled from the **live GitHub
> API on 2026-06-21** (not from model memory). Re-verify before acting if read much later.

---

## Part A — Architecture comparables (the senior-RAG-architect brief)

### Brief (what we asked)
Find mature, actively-maintained, **self-hostable / local-LLM** open-source projects to learn from.
Our system: QA over SQLite with two layers — (1) a **knowledge graph** answered by deterministic
parameterized SQL "skills" (enumerate/filter/traverse/count/rank-by-metric), and (2) a **semantic KB**
(vector KNN + FTS, fused with RRF, cross-encoder rerank, local LLM composes). A **deterministic
rule-based router** runs first; misses fall to a live web-search fallback. Hard constraint:
**everything local** (Ollama / Llama 3.1 8B, local embeddings/rerankers — no cloud LLM). Core
problem: the **rule-based router is brittle**; considering a **grounded LLM intent/slot gate** (one
cheap local call → `{intent, metric, org, n, order}`, schema-grounded, confidence floor → hand off to
deterministic skills so numbers stay exact).

### ⚠️ Honesty flags (time-sensitive, verified today)
- **Vanna (vanna-ai/vanna) is ARCHIVED** (owner archived 2026-03-29, read-only). Best *pattern* fit
  for our problem, but **do not depend on the repo** — mine the approach.
- **Verba (weaviate/Verba) is ARCHIVED** — drop it.
- **Quivr** (last push 2025-07-09) and **R2R** (2025-11-07) have gone stale / pivoted — deprioritize.

### Verified shortlist (ranked by relevance to the router problem)

| Repo | ⭐ / last push / license | What it is | Routing/intent | KG+vector+rerank | Local? 8B? | Rel |
|---|---|---|---|---|---|---|
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | 50.3k · 2026-06-20 · MIT | RAG framework | `RouterQueryEngine` + `LLMSingleSelector`/`PydanticSingleSelector`; `SubQuestionQueryEngine` | `PropertyGraphIndex` w/ `TextToCypherRetriever` + `VectorContextRetriever`; rerank postprocessors (bge/ColBERT local) | Local YES (Ollama + HF embeds). 8B: selectors fine; text-to-Cypher/SQL brittle w/o schema grounding | **5** |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | 25.6k · 2026-06-19 · Apache-2.0 | Composable pipeline framework | Routing is a first-class component: `ConditionalRouter`, `TransformersZeroShotTextRouter`, LLM routers, branching pipelines | Hybrid retrievers + local rerankers; graph via integrations | Local YES (Ollama, local rankers). 8B: zero-shot/classifier routers need no LLM | **5** |
| [neuml/txtai](https://github.com/neuml/txtai) | 12.7k · 2026-06-19 · Apache-2.0 | Embeddings DB = vectors + SQL + graph + relational in ONE store | Agents/workflows orchestrate; SQL layer deterministic | Union of sparse+dense vectors, graph networks, relational + rerank pipeline | Local YES by design (HF/ST, llama.cpp). 8B ok | **5** (closest architectural twin to our "SQL skills + vectors in one DB") |
| [neo4j/neo4j-graphrag-python](https://github.com/neo4j/neo4j-graphrag-python) | 1.2k · 2026-06-16 · Apache-2.0 | Official Neo4j GraphRAG lib | **`Text2CypherRetriever`** = canonical NL→exact structured query; Hybrid/VectorCypher retrievers | Vector + fulltext hybrid + Cypher traversal | Local YES (Ollama + sentence-transformers). 8B: inherits same text→query brittleness | **4** (productionized version of our exact problem; needs Neo4j) |
| [eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT) | 19.0k · 2026-06-19 · MIT | Privacy-first text-to-SQL + agentic data-app framework (AWEL) | Agent/workflow routing; GraphRAG module | Text-to-SQL + GraphRAG | Local YES (built for private deploy; ships fine-tuned small text-to-SQL models, DB-GPT-Hub). 8B better via FT | **4** |
| [vanna-ai/vanna](https://github.com/vanna-ai/vanna) ⚠️ARCHIVED | 23.6k · 2026-02-02 · MIT | Text-to-SQL via RAG-on-schema | Retrieve similar (schema DDL + docs + Q→SQL examples) → generate | Vector store of training examples | Local YES (Ollama+Chroma). 8B: the whole point — RAG grounding makes small models emit correct SQL | **4 (pattern only — repo dead)** |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) | 36.8k · 2026-06-18 · MIT | Lightweight dual-level (entity+theme) graph RAG | No intent router; dual-level retrieval | KG + vector hybrid, lighter than MS GraphRAG | Local YES (Ollama documented). 8B usable | 3 |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | 33.9k · 2026-06-19 · MIT | LLM-built entity graph + community summaries | User picks local-vs-global search (not auto-routed) | Map-reduce over graph communities | Partial — runs on OpenAI-compat/Ollama but indexing LLM-heavy; 8B degrades graph-extraction | 3 (we already have a curated KG — don't need auto-extraction) |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 83.3k · 2026-06-21 · Apache-2.0 | Deep-doc-understanding RAG server + agents | Agent-flow routing (doc-centric) | GraphRAG + rerank built in | Local YES. Heavy server product | 3 |

Also examined: topoteretes/cognee (18.5k, memory/ECL layer), QuivrHQ/quivr (39.2k but stale 2025-07),
SciPhi-AI/R2R (7.9k, stale 2025-11) — deprioritized.

### Deep dig — 3 best LOCAL fits for the router problem
**A. LlamaIndex `RouterQueryEngine` — direct analog to our router.** Wraps query engines as tools and
uses a **selector** to pick one. `PydanticSingleSelector` makes the LLM emit a **structured choice**,
not free text → constrain + validate on 8B. Exactly our "intent gate that hands off to deterministic
skills." Register each KG skill as a described tool; cheap local selector chooses; semantic engine is
the low-confidence default. (`llama_index.core.selectors`, `RouterQueryEngine`.)

**B. Haystack — routing as an explicit, often NON-LLM component.** Lesson: most routing shouldn't need
a generative model. `TransformersZeroShotTextRouter` (tiny local classifier) or `ConditionalRouter`
(rules) sit before retrieval. Maps onto our cue-gate bug: the fix may be "make tier-1 a real
classifier" not "add an LLM." Cheap, deterministic, 8B-free.

**C. Vanna's RAG-on-schema pattern (archived repo, living idea) — make an 8B emit EXACT structured
output.** Retrieve (1) schema DDL, (2) docs, (3) prior `(question → query)` exemplars; feed as context.
For us: retrieve our **real org names, metric names, and a few `(question → {intent,metric,org,n,order})`
exemplars** → small model fills slots far more reliably. **Neo4j `Text2CypherRetriever` is the
maintained, productionized version** of this idea if we want living code to read.

### Synthesis & recommendation (privacy-constrained, local-only)
- **(a) structured-vs-semantic routing:** two-tier router. Tier 1 = cheap deterministic / zero-shot
  classifier (Haystack). Tier 2 = LLM selector with **structured output**, only for ambiguous queries
  (LlamaIndex). Confidence floor → semantic RAG. Nobody mature routes on hand-keyed cue words alone —
  that is precisely our brittleness.
- **(b) grounded LLM intent layer on a small model:** grounding + constrained decoding + validation,
  never free generation —
  1. **Constrain output** — Ollama `format: json` / llama.cpp GBNF / Outlines → model can only emit our
     `{intent, metric, org, n, order}` schema.
  2. **Ground it** — inject real schema + entity/metric lists + retrieved exemplars (the Vanna move).
  3. **Validate every slot** against the DB before dispatch; confidence floor → semantic fallback.
  4. LLM only *picks* intent + slots — **the deterministic skill still computes the numbers** → exact.
- **Concrete recommendation (staged):**
  - **Now (the bug):** fix Tier 1 first — extend the cue tuple (already-specified quick fix) *or* swap
    for a tiny zero-shot classifier. Kills "top 10 by citations in X" mis-routes without an LLM.
  - **Next (robustness):** add the Tier-2 grounded JSON intent/slot gate (Ollama `format=json`,
    entity-list grounding, slot validation, confidence floor), invoked only when Tier 1 is unsure.
    This is our proposed design, backed by LlamaIndex selectors + Vanna/Neo4j Text2Cypher patterns.
  - **Don't** adopt MS GraphRAG / LightRAG auto graph-extraction — our KG is hand-curated and more
    reliable than 8B-extracted graphs; their value (community summaries) doesn't address routing.

### Sources (Part A)
- https://github.com/run-llama/llama_index
- https://github.com/deepset-ai/haystack
- https://github.com/neuml/txtai · https://neuml.github.io/txtai/
- https://github.com/neo4j/neo4j-graphrag-python
- https://github.com/eosphoros-ai/DB-GPT
- https://github.com/vanna-ai/vanna (ARCHIVED)
- https://github.com/HKUDS/LightRAG
- https://github.com/microsoft/graphrag
- https://github.com/infiniflow/ragflow

---

## Part B — "Similar projects for ideas" (product-idea comparables)

Closest analogs and what to borrow:
- **HelixDB — GraphRAG for Professor Recommendations** — literally our "Find Your Advisor": Graph-Vector
  RAG = exact graph filters (dept/university) + semantic similarity over research interests. Direct
  blueprint for `feat/find-your-advisor`.
  https://www.helix-db.com/blog/building-a-graphrag-system-for-professor-recommendations-with-helixdb
- **REBot / CatRAG (arXiv 2510.01800)** — hybrid RAG + GraphRAG, category-labeled KG + "graph routing";
  validates our router→structured-vs-semantic design and gives a recipe for the *deferred*
  external-profiles bullet 3 (Scholar interests → ResearchArea/`researches` edges via semantic node
  enrichment). https://arxiv.org/pdf/2510.01800
- **NU Chatbot (Northeastern)** — privacy-preserving local RAG; same deployment posture.
  https://www.researchgate.net/publication/398984671
- **Marcel (arXiv 2507.13937)** — open-source, resource-constrained university support agent.
  https://arxiv.org/pdf/2507.13937
- **SmartCampusBot** — local hardware, "edit JSON + re-index" update loop = our dashboard-edit +
  `embed_all`; coverage of aid/facilities/services nudges the day-to-day-intents expansion.
  https://medium.com/@ashwinnehete/building-smartcampusbot-a-private-rag-powered-assistant-for-modern-universities-3f3a17eeae79
- **Multi-Agent RAG for admissions counseling (arXiv 2507.11272)** — relevant if we revisit an LLM
  intent/slot gate. https://arxiv.org/pdf/2507.11272
- **Unimib Assistant (arXiv 2411.19554)** — student-friendly RAG chatbot. https://arxiv.org/pdf/2411.19554

**Takeaway:** two already-approved ideas now have published blueprints — **Find Your Advisor**
(HelixDB Graph-Vector fusion) and the **deferred Scholar-interests→ResearchArea edges** (CatRAG node
enrichment). Nothing suggests a pivot; it confirms the architecture.

---

## Part C — Other LLMs' searches

### C.1 — Claude (web), 2026-06-21

**Cross-check verdict: strong corroboration, one factual error caught.**

Agreements with our Part A (independent confirmation): layered/two-tier routing is the consensus;
keep the LLM OFF the numeric path (route/translate only, DB computes → exact); ground the intent call
in real schema + entity lists + few-shot Q→query exemplars; prefer constrained JSON over function-
calling on 8B models; LlamaIndex `RouterQueryEngine`/selectors, Haystack routers, neo4j
`Text2CypherRetriever`, txtai, RAGFlow are the right candidates; skip MS GraphRAG/Cognee auto-graph
extraction for our exact-KG case.

**⚠️ ERROR CAUGHT (Vanna):** Claude-web claimed Vanna is "clearly active (Vanna 2.0, Mar 2026)" and
dismissed the archive note. **Re-verified via GitHub API 2026-06-21: `archived=True`.** What actually
happened — Vanna 2.0 shipped (v2.0.2, 2026-02-02) and the repo was then **archived 2026-03-29**. The
2.0 rewrite was real; ongoing maintenance is not. **Our "ARCHIVED" flag stands** — mine the pattern,
don't depend on the repo. (Lesson: a recent release tag ≠ maintained; check `archived`.)

**Star-count note:** Claude-web's figures are approximate/slightly stale (GraphRAG 31.8k vs our live
33.9k; LlamaIndex ~45k vs 50.3k; Haystack ~21k vs 25.6k). Trust the Part A table (live API today).

**Net-new / sharper points worth keeping from Claude-web:**
- **Framing:** most "GraphRAG" projects build the graph by *LLM-extracting entities from text* for
  *semantic* retrieval — the OPPOSITE of our authored, exact KG. So for our KG layer the relevant
  analog is **text-to-structured-query + LLM routing/intent gating**, NOT GraphRAG. (Good lens.)
- **Haystack ladder (most concrete for OUR bug), all local:**
  - `ConditionalRouter` — deterministic Jinja rules; the documented pattern for **RAG-miss → web-search
    fallback** (mirrors our live fallback). Tutorial: building-fallbacks-with-conditional-routing.
  - **`TransformersTextRouter`** — a SMALL fine-tuned BERT classifier (e.g.
    `shahrukhx01/bert-mini-finetune-question-detection`, few MB) branches keyword-vs-question with
    **NO LLM, ~ms latency**. Can fine-tune our own "structured-KG-intent vs semantic" head on a few
    hundred of our own queries. → This is the "tier-1 classifier" in our synthesis, named + sourced.
  - `TransformersZeroShotTextRouter` — zero-shot labels if we don't want to train.
- **LlamaIndex caveat (8B-specific):** use `LLMSingleSelector` (JSON), NOT `PydanticSingleSelector`
  (function-calling) — 8B models are unreliable at OpenAI-style function calling but fine at
  constrained-JSON selection. SQL-vs-vector router example: examples/query_engine/SQLRouterQueryEngine.
- **neo4j `Text2CypherRetriever`:** their internal studies found it the MOST consistent retriever
  across phrasings — directly addresses our brittleness; grounding = schema string + (question,query)
  example pairs + output sanitization. Worth reading the source even if we stay on SQLite. Also new:
  `ToolsRetriever` / `Retriever.convert_to_tool()` for LLM-pick-among-retrievers.
- **RAGFlow:** strongest reference for *hybrid retrieval + fused reranking at scale* (multi-recall +
  fused re-rank, BCE/BGE/Jina rerankers, Ollama/vLLM); but heavy stack (ES/Infinity+MySQL+MinIO,
  ≥16GB) — read, don't adopt. Note: it now ships Discord/Telegram bot channels (relevant to Gateway).
- **txtai:** confirmed closest *architectural* sibling (SQLite+FAISS under the hood); its `neuml/rag`
  app explicitly offers "Vector RAG" vs "Graph RAG"; demoed with tiny models (Qwen3-0.6B).
- Claude-web's recommendation converges on ours: **three-tier local gate** = Tier 0 deterministic rules
  (`ConditionalRouter`) → Tier 1 small local classifier (`TransformersTextRouter`, fine-tuned on our
  queries) → Tier 2 grounded JSON slot-gate (Ollama, schema+entity-list injected, exemplars, confidence
  floor) → hand to deterministic skills; semantic RAG + web fallback unchanged. Read LlamaIndex/neo4j/
  Vanna for patterns; Haystack is the one it would actually run (least disruption, fully local).

Claude-web links (primary sources):
- LlamaIndex router: https://developers.llamaindex.ai/python/framework/module_guides/querying/router/
  · SQL-vs-vector example: https://docs.llamaindex.ai/en/stable/examples/query_engine/SQLRouterQueryEngine/
- Haystack `ConditionalRouter`: https://docs.haystack.deepset.ai/docs/conditionalrouter
  · fallback tutorial: https://haystack.deepset.ai/tutorials/36_building_fallbacks_with_conditional_routing
  · `TransformersTextRouter`: https://docs.haystack.deepset.ai/docs/transformerstextrouter
  · routing tutorial: https://haystack.deepset.ai/tutorials/41_query_classification_with_transformerstextrouter_and_transformerszeroshottextrouter
- neo4j Text2Cypher source: https://neo4j.com/docs/neo4j-graphrag-python/current/_modules/neo4j_graphrag/retrievers/text2cypher.html
  · RAG user guide: https://www.neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html

### C.2 — ChatGPT, 2026-06-21

**Cross-check verdict: strong corroboration + TWO valuable net-new candidates (both verified live).**

Agreements: independently flagged **Vanna ARCHIVED 2026-03-29** and **Verba ARCHIVED 2026-06-08**
(corroborates us; confirms Claude-web was the outlier on Vanna). Same layered-gate consensus: cheap
routing first → LLM as *parser not executor* → schema/entity grounding → strict validation →
deterministic SQL execution → semantic RAG only for narrative. Same local-model floor finding (~7B is
the practical threshold for reliable structured output; <7B fails — use Llama-3.1-8B/Qwen-7B for
constrained slot extraction, NOT autonomous SQL/KG construction).

**⭐ NET-NEW candidate #1 — Semantic Router (`aurelio-labs/semantic-router`)** — VERIFIED live: 3.6k
stars, v0.1.15 (2026-05-23), **not archived**, MIT. *The most direct, lightweight, fully-local fix for
our exact brittle-keyword-router problem.* A "semantic if/else" decision layer: define ONE route per
deterministic KG skill, give each route paraphrase utterances, embed the query locally
(`HuggingFaceEncoder`), dispatch only when the top route clears a tuned threshold+margin, **return
None → fall through to RAG** (exactly our desired pre-RAG behavior). Embedding-only first stage = no
LLM on the hot path, ideal for small/local. **This is the strongest practical option for the immediate
fix** and slots in front of our existing skills without adopting a heavy framework.

**⭐ NET-NEW candidate #2 — WrenAI (`Canner/WrenAI`)** — VERIFIED live: 15.6k stars, pushed 2026-06-21,
wren-v0.10.0 (2026-06-20), **not archived**, Apache-core. Governed text-to-SQL via a **semantic context
layer**: semantic definitions + examples + memory + **dry-plan validation** + structured errors + row
limits. Transferable IDEA (not the stack): "semantic model as code + dry-run validation + error repair"
— a design reference for keeping NL→structured honest. Has an Ollama+Llama tutorial (local-capable).

ChatGPT's other rankings track ours: LlamaIndex 5/5 (router + structured output; use JSON selector,
validate), Haystack 4.5/5 (`ConditionalRouter` + on-prem cross-encoder rankers — best pipeline shell),
DB-GPT 4/5, MS GraphRAG 4/5 (read, don't adopt — our KG is exact/curated), LightRAG 4/5, txtai 4/5
(closest architectural sibling), R2R 3.5/5 (notes latest release v3.6.5 Jun-2025 → stale, cloud-default),
RAGFlow 3.5/5. neo4j-graphrag = read if we ever move off SQLite.

**ChatGPT's concrete arch (converges with ours):** keep deterministic SQL skills; replace brittle rule
router with a **three-stage gate** = high-precision regex/rules → **local semantic route classifier
(Semantic Router or own sentence-transformer route vectors)** → **local JSON slot extractor (Ollama,
forced schema, grounded with candidate entity IDs pulled from SQLite alias/FTS lookup)** → execute
prewritten SQL skills. Key emphasis: the LLM **selects only from candidate IDs we supply** (never
invents orgs/people/metrics/SQL), and Ollama structured-outputs (`format`=JSON schema) enforces the
`{intent, metric, org_id, area_id, n, order, confidence}` shape. Don't adopt general text-to-SQL as the
primary path for exact KG questions; use WrenAI/DB-GPT only as design refs for schema-context + dry-run
validation. Semantic KB layer stays Haystack/txtai/LightRAG-style: local embeds + FTS/BM25 + RRF +
local cross-encoder rerank + grounded composer.

ChatGPT primary-source links:
- Semantic Router: https://github.com/aurelio-labs/semantic-router
- WrenAI: https://github.com/Canner/WrenAI
- Ollama structured outputs: https://ollama.com/blog/structured-outputs
- LlamaIndex routers: https://developers.llamaindex.ai/python/framework/module_guides/querying/router/
- Haystack ConditionalRouter: https://docs.haystack.deepset.ai/docs/conditionalrouter
  · ranker choice (on-prem vs API): https://docs.haystack.deepset.ai/docs/choosing-the-right-ranker
- GraphRAG-on-consumer-hardware (~7B floor) benchmark: https://arxiv.org/abs/2605.20815

---

## Three-LLM convergence (Claude-CLI + Claude-web + ChatGPT)
**Unanimous, independently:**
1. Keep deterministic SQL skills; the LLM **routes/parses, never computes** numbers → exactness preserved.
2. Replace the brittle keyword router with a **multi-stage gate**: cheap deterministic rules → a cheap
   learned router (embedding semantic-route OR small classifier) → a grounded JSON slot-gate **only when
   unsure** → deterministic skill; semantic RAG + web fallback unchanged.
3. **Ground the LLM call** in real schema + entity/metric ID lists; **constrain output** (Ollama
   `format=json` / GBNF); **validate every slot** against the DB; **confidence/margin floor** → fallback.
4. ~7–8B is fine for routing/slot-filling, NOT for autonomous SQL or KG auto-extraction.
5. **Skip** MS GraphRAG / LightRAG auto-graph-extraction (our KG is curated & exact). **Drop** Vanna +
   Verba (archived). Semantic KB layer: hybrid + RRF + local cross-encoder rerank (already have).

**The two implementation routes the models split on (the real decision):**
- **(i) Embedding semantic-route classifier** as tier-1 — Semantic Router (ChatGPT) or Haystack
  `TransformersTextRouter` (Claude-web). Cheapest, no LLM, ms latency; needs labeled route utterances.
- **(ii) Grounded LLM JSON slot-gate** as tier-2 (all three) — Ollama structured output + entity-list
  grounding + validation. Higher fidelity on novel phrasings; one cheap local call when tier-1 is unsure.
Consensus = do BOTH in sequence (i then ii), tier-0 rules in front. Quick win for the KNOWN bug remains
the metric/rank cue fix in `_try_structured` (see [[project_router_robustness]]).

---

## Part D — Decision (2026-06-21)

**This work is the Kavosh v2.1 milestone** (brand = GSA Gateway; version = Kavosh, "exploration" in
Persian; v2.0 → v2.1). Theme of v2.1 = **router robustness** (de-brittle the structured-vs-semantic
routing so metric/rank/NL-phrased queries reach the exact KG skills). [[project_kavosh_persona]]
[[project_router_robustness]]

### Track 1 — DEFERRED to backlog (quick win, recorded for future)
The already-diagnosed metric/rank **cue fix** in `bot/core/message_handler.py` `_try_structured`
(lines ~286-298): the cheap pre-gate's cue tuple has ZERO metric/rank words, so a >4-word query like
"top 10 by citations in mechanical engineering" hits no cue → skips structured routing → RAG → live
search → wrong answer. Fix = add `"citation","cited","h-index","h index","i10","top ","most ","rank",
"scholar"` to the tuple (+ singular-"citation" alias in `profile_fields.match_metric`; optional: a
no-metric "top 10 in X" defaults to citations). Small + precise; verify on the failing live set THROUGH
the handler path (NOT `ask.sh`, which bypasses the pre-gate). **Status: deferred — do later as a fast
standalone PR. Track 2 supersedes the pre-gate entirely, so if Track 2 lands first, Track 1 may be
absorbed.** Still goes through the expert-review HARD GATE.

### Track 2 — IN PROGRESS (the Kavosh v2.1 build) — design via brainstorming now
Replace the brittle keyword router with the consensus **multi-stage gate** (all three LLMs converged):
deterministic rules → cheap learned router (embedding semantic-route OR small classifier) → grounded
LLM JSON slot-gate (only when unsure) → deterministic KG skills; semantic RAG + web fallback unchanged.
**Key reuse:** slot grounding already exists in `router.py` (`_org_candidates`, `entity.persons_in_query`,
`profile_fields.match_metric`) — the LLM picks INTENT, resolution stays deterministic against real DB
lists → numbers never touch the LLM (exactness preserved). Candidate building blocks to learn from:
`aurelio-labs/semantic-router` (tier-1 lib), Haystack `TransformersTextRouter` (classifier alt),
LlamaIndex `LLMSingleSelector` + Ollama `format=json` (tier-2 structured output), neo4j `Text2Cypher` /
Vanna RAG-on-schema (grounding pattern). Design doc to be written to
`docs/superpowers/specs/2026-06-21-kavosh-v2.1-router-gate-design.md`. **HARD GATE applies:** design →
senior-eng + RAG/anti-fab review (checked vs goals) → Mohammad approves → TDD → diff → sign-off → ship.

### Product ideas (noted, not started)
Find Your Advisor (HelixDB Graph-Vector blueprint) [[project_find_your_advisor]] and the deferred
external-profiles bullet-3 (Scholar interests → ResearchArea/`researches` edges, CatRAG enrichment)
remain on the backlog — separate from Kavosh v2.1.
</content>
</invoke>

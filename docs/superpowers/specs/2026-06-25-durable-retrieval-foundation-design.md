# Durable Retrieval Foundation — Design Spec

**Date:** 2026-06-25
**Status:** REVISED — locked idea + 2 adversarial pressure-tests + 2 formal HARD-GATE reviews (senior-eng + RAG, both **GO-WITH-CHANGES**) all folded. Awaiting owner sign-off, then `writing-plans` (5 plans, §14) → TDD build. Both reviews verified every numeric claim against the live DB as exact.
**Owner:** Mohammad Dindoost. Claude in charge of driving ("lock it do it. you are in charge").
**Supersedes the patch framing of:** M2 embedding truncation (`docs/.../2026-06-2X-m2-chunk-embedding-design` — never written) and the office-routing dilution regression (open-items #0). Both are absorbed here and fixed from root.

---

## 1. Problem & Goals

### The problem
The semantic leg of retrieval embeds only the first **2000 characters** of each item into **one vector per item** (`embed_all.py:76`, `embedder.py:66`). Long pages are semantically blind past ~2000 chars. Measured on the live DB: of the **6,086 served (non-publication) items, 1,055 exceed 2000 chars — 956 of them `policy` prose** (exactly the college/office pages the crawler exists to bring). The keyword (FTS) leg indexes full text, so deep content is keyword-findable but not semantically. Separately, adding hundreds of college-prose pages diluted office routing ("who handles a registration hold" ranks a college page above the Registrar).

### Goals (each must be delivered or LOUDLY deferred — see §11 checklist)
1. **G1 — Kill embedding truncation at root.** Represent the *full* content of every item, not a 2000-char slice.
2. **G2 — Fix office-routing dilution structurally**, not with a keyword×category band-aid (honors `feedback_no_bandaid_align_data_and_retrieval`).
3. **G3 — Make the two hard lines structural**, not conventions:
   - **LLM-agnostic** (`feedback_llm_agnostic`): any embedding/generation model swaps via config + re-embed, no rewrite.
   - **Max-capacity** (`feedback_use_max_capacity`): use the active model's *real* window; leave no capability idle — at index time AND query time.
4. **G4 — One ingestion engine** producing both people/graph and prose, for offices + colleges + departments + faculty, mechanical-clean only (honors the crawl hard line).
5. **G5 — Rebuild the crawler-derived data on the new schema WITHOUT losing any non-crawler data** (immortal posts/judging, manual people, Scholar metrics, settings, analytics).

### Non-goals (explicitly out of scope here)
- Making GSA / MMI / clubs crawlable (owner: stays manual for now; future migration).
- PDF extraction, faculty personal-page CONTENT crawl, Find-Your-Advisor (separate backlog).
- Swapping the embedding model or generation model (the design *enables* it; we don't do it).

---

## 2. Evidence base

The foundation (everything except the fenced Contextual-Retrieval pilot) rests on vendor-neutral RAG literature, NOT on Anthropic:
- Chunk size/overlap: Chroma chunking benchmark (tuning beats method; 50%-overlap is an anti-pattern); Pinecone (~512 + 10–20%); Nomic technical report (nomic-embed-text MTEB-evaluated at 512, native window 2048); HF nomic tokenizer-mismatch pitfall.
- Parent-document / small-to-big: LangChain `ParentDocumentRetriever`, LlamaIndex auto-merging (gains "modest" — context-coherence, not guaranteed accuracy).
- Fusion: Cormack 2009 RRF (k=60); AI21 multi-scale (chunks vote for parent, fuse on **rank**, collapse before fusion); Bruch et al. arXiv 2210.11934 (RRF is tunable, not magic; reranker corrects granularity artifacts).
- Grids: arXiv 2508.21038 (dense retrieval structurally weak on near-identical rows); Microsoft/LlamaIndex (route exact/aggregation to keyword/SQL).
- Invalidation: LangChain `source_id_key` / Pinecone `doc_id#chunk` / LlamaIndex `ref_doc_id` (delete-all-chunks-for-parent + content-hash + orphan GC).
- §8 only — Anthropic Contextual Retrieval (run on LOCAL llama, so not a lock-in).

Two adversarial pressure-tests (2026-06-25) read the actual code + live DB and reshaped this spec; their findings are folded inline and tagged **[ARCH Rn]** (architecture/retrieval red-team) and **[DATA Rn]** (data-safety red-team).

---

## 3. Architecture overview

**One sentence:** content is *found* by small precise chunks but *served* as the full parent; both retrieval legs rank parent **items**; grids and structured facts leave the vector path; every size/limit is derived from the active model; and the rebuild regenerates only crawler-derived data on a copy of the live file so nothing else is lost.

Four subsystems (each becomes its own implementation plan via writing-plans):
- **A. Representation contract** (§4) — the data model everything binds to.
- **B. Ingestion engine** (§5) — one crawler → clean → store.
- **C. Embedding/chunking layer** (§6) — model-descriptor-driven chunking + invalidation.
- **D. Retrieval/ranking layer** (§7) — chunk-KNN → collapse → RRF → rerank → serve parent; structured office routing.
- Cross-cutting: **migration/cutover** (§8), **eval** (§9).

---

## 4. A. Representation contract (the root)

- `knowledge_items` stays the **parent** — the full page; **always** what the LLM receives (honors never-withhold).
- **New `knowledge_chunks` table:** `(id, parent_id, source_key, ordinal, text, content_hash, model_id)`.
  - `parent_id REFERENCES knowledge_items(id) ON DELETE CASCADE` is a **backstop for the rare hard-DELETE path only** — it is NOT the primary invalidation mechanism. The system **soft-deletes** (`reconcile.py:130–151` sets `is_active=0` and inserts a *new* row; the old id persists), so cascade essentially never fires. Primary invalidation = reconcile's superseded/deactivated sets + the `is_active`-keyed GC sweep (§6) **[SE HIGH-2]**.
  - `model_id` records which embedding-model descriptor produced the chunking, so a descriptor change is detectable.
- **New chunk vector table** (`vec0`) — one vector **per chunk**, with **metadata/partition columns declared on the vec0 table so KNN can be filtered in-engine [RAG I2 / SE LOW-6, verified feasible in sqlite-vec v0.1.9]:** `org_id` as a **partition key**, `type` as a **metadata column**, `parent_id` as an **auxiliary `+column`** (for collapse). The embedding `dim` is read from the model descriptor (§6), not hardcoded 768 — but note `dim` is fixed at `CREATE TABLE`, so a dimension change means **recreate the vec0 table + full re-embed** (a re-embed already implies this).
- **Item-level FTS** (existing `knowledge_fts`) is the keyword leg. **No chunk-level FTS** — §7's keyword leg fuses item-level only, so a chunk-FTS table would be dead weight (index + write cost for an unused signal) **[SE MEDIUM-4]**.
- **Provenance:** KB provenance is `created_by` (NOT a `source` column — `knowledge_items` has none **[DATA correction]**); nodes/edges carry `source`. Keep **distinct `created_by` per ingestion engine** so source-scoped reconcile never cross-wipes **[ARCH R6/DATA R6]**.
- **`organizations.type`** already distinguishes office(12)/department(16)/college(6) — this is the `org_kind` signal used by §7's structural routing; it exists, no new column **[ARCH R2]**.
- **Uniform pipeline:** every item → ≥1 chunk; a short item is exactly one chunk (identical content to today). Corpus-wide growth ~1.17×, but the **served corpus grows ~1.6×** (6,086 → ~9,758 chunks) — quote the *served* ratio, size pools against it **[ARCH R9]**.

---

## 5. B. Ingestion engine

One engine produces ALL crawler knowledge: people+roles+research-areas (absorbing `explore.py`) AND prose (absorbing `college_crawl`), for offices + colleges + departments + faculty.

- **Mechanical-clean only**, one direction (clean → store), no serving/gating decisions (crawl hard line).
- Every page carries a **stable `source_key` + `content_hash`**.
- **Absorbed `explore.py` rules MUST be ported as explicit, tested invariants** (each lost = a regression) **[ARCH R6 / DATA R6]**:
  - home-appointment-only / section routing (`explore.py:185–203`),
  - parent-before-child org ordering (`entry_points.py:97–107`),
  - MTSM "no `type='department'` children" invariant (enforced by `verify_kg.py:77–86`),
  - `merge=True` first-touch-overwrites gating (`project.py:132–147`) — must MERGE not clobber manual Scholar metrics,
  - reconcile move-vs-departure KB re-file (`explore.py:390–420`),
  - transient-empty-decomposition guard (`reconcile.py:146`),
  - distinct `created_by` per engine for source-scoped reconcile (`reconcile.py:97–103`).
- **`entity_id` derivation is FROZEN as a contract [ARCH R5 / DATA R5]** — re-applied Scholar metrics key to Person nodes by `entity_id`; any drift (slug/diacritic normalization) orphans 211 metric records. Acceptance: 0 orphaned profile keys after re-seed.
- `verify_kg` + `verify_gsa` stay green as a hard gate.

---

## 6. C. Embedding / chunking layer

- **Model descriptor — single source of truth [G3].** One object: `{name, tokenizer, context_window, working_size, dim, doc_prefix, query_prefix}`. Chunk budget, overlap, truncation, and vec dimension all read from it. The embed call is a **provider-isolated seam**. Swap = new descriptor + re-embed. **`context_window` (hard truncation guard) and `working_size` (chunk target) are SEMANTICALLY DISTINCT and must not be conflated [RAG I3]:** max-capacity means "use the model in its strongest *measured* regime" (`working_size`=512 for nomic, its MTEB regime), NOT the raw context ceiling (2048/8192 via NTK interpolation) — embedding at the ceiling *lowers* quality.
- **Chunking rule:** structure-aware (split on headings where present; recursive-character fallback). **No semantic/LLM boundary detection** (research: modest gain, heavy cost). **~512 tokens, ~15% overlap, measured with nomic's OWN tokenizer** (tiktoken mismatch is a real pitfall) — values from the descriptor's `working_size` (512 for nomic, inside its native window, matching its MTEB regime).
- **Eliminate the `[:2000]` slice in all FOUR sites** (`embedder.py:63,66`, `embed_all.py:76,80`) via one shared helper that truncates at the descriptor's window, not a constant.
- **Invalidation — reconcile-driven, not FK-driven [SE HIGH-2 / ARCH R8]:**
  - content-hash skip when unchanged; on a content change a NEW item row is created and the old set `is_active=0` (existing reconcile behavior) → the chunker drops the **superseded** item's chunks and chunks the new row → re-embed (local/free → full-replace is simplest-correct).
  - **The primary GC is an `is_active`-keyed sweep**: delete chunks (and their vectors, since vec0 can't FK) whose parent item is absent OR `is_active=0`. This is the **#1 missed step** and fixes a CURRENT bug — **~891 orphan vectors already leak today** under the one-vec-per-item pipeline.
  - **Invariant test** (verify_kg-style, the enforcement — not an assertion): `count(chunk vectors without an active parent) == 0`.
  - **Enumerated writers** the invalidation/GC must cover: `reconcile_entity`, `reconcile_departures`, `people_editor`, dashboard `POST /knowledge` & `POST /people`, `embed_all --force`. (FK cascade covers only a literal hard-DELETE, which these do not do.)
- **Re-embed cost is a first-class cutover concern [SE MEDIUM-5]:** the mandatory chunked re-embed is **~25,500 chunk embeddings** (~15,722 ~1-chunk publications + ~9,758 served chunks). `embed_all.py:171–188` is **serial** (0.05s sleep, 30s timeouts, one retry) → plausibly 1–3h. `Embedder._embed_batch` (Ollama list `input`) already exists but `embed_all` doesn't use it — **batch the embed pass** to cut this materially (also de-risks the §9 reindex).

---

## 7. D. Retrieval / ranking layer

### Pipeline (order matters)
1. **Semantic leg:** query → chunk-KNN. **Over-fetch is dynamic [ARCH R1]:** fetch until ≥ `pool_size` *distinct parents* recovered OR a cap — NOT a fixed 4×, because hits concentrate in big multi-chunk pages and a fixed factor starves parent diversity.
2. **Collapse chunk→parent by BEST (min-rank) child** — never sum (sum re-introduces long-doc bias and worsens office dilution). *Note the residual: collapse fixes score-aggregation bias, not the **fetch/opportunity** bias (a 20-chunk page gets 20 KNN lottery tickets); the dynamic over-fetch + a per-item chunk cap address that.* **[ARCH R1]**
3. **Keyword leg:** item-level FTS bm25.
4. **RRF fuse** the two **item-level** lists. RRF is rank-based / scale-invariant, so there is no "score scale" to tune **[RAG I5]** — instead two distinct tasks: **(a) re-derive the multiplicative priors** (NEWS_FLOOR 0.5 / EVENT_BOOST 1.2 / WEBPAGE 0.8) against the new post-RRF magnitudes; **(b) re-validate RRF `k` and the asymmetric `RERANK_CE_K=10`** against the new rank distribution (fewer distinct parents). Optionally A/B a score-based convex combination as the fusion alternative (Bruch et al.: more robust to the domain shift a rebuild causes).
5. **Priors** (multiplicative). **Audit the double-application [ARCH R7]:** `decay_for` is applied at both `retriever.py:432` and `:285`; re-derive prior constants (NEWS_FLOOR 0.5, EVENT_BOOST 1.2, WEBPAGE 0.8) against the new RRF magnitudes, and make application once-per-stage by design.
6. **Cross-encoder rerank on the matched chunk + a small neighbor window** (not the full page, which is CE-truncation-blind today at `retriever.py:272`; not the lone chunk, which under-ranks multi-span answers). Score top-2 child chunks, take max. **A/B before locking — do not assume chunk-CE ≥ full-CE [ARCH R4].**
7. **Diversify**, then **hydrate the FULL parent** to the LLM.

### Fix the hidden query-time truncation [ARCH R3 — must ship in the same change]
`_VEC_KNN_MAX=4096` with **fetch-then-filter** covers only ~16–19% of the (now larger, chunked) vector space; org-filtered queries whose chunks aren't in the global top-4096 get a silently-empty semantic leg. **Push the org/type filter INTO the vec0 KNN via the declared partition/metadata columns** (§4: `org_id` partition key, `type` metadata column) — NOT a `rowid IN (...)` list (which explodes the host-param count on org-scoped sets of thousands; SQLite's 32,766 param cap is survivable but slow to build per-query). **Verified feasible in-process on sqlite-vec v0.1.9 [SE]** — a filtered `LIMIT k` returns the true k-nearest *within scope* (vec0 is exact brute-force), which dissolves the cap rather than patching it. The stale comment claiming "top ~83%" must be corrected.

### Office-routing dilution — STRUCTURAL fix [G2 / ARCH R2 / RAG I1 / SE MEDIUM-3]
**The crux both reviewers flagged:** a failing query like "who handles a registration hold" **names no org**, so the router's name/slug/alias resolution (`router.py:208`) cannot scope it. The missing piece is an explicit **procedural-intent → unit resolver**, and the design must NOT pretend org-name scoping alone covers it. The honest distinction from the rejected band-aid: the band-aid was a `cue × org_kind` **multiplier applied to the score**; this is **intent→unit resolution that selects/scopes the candidate set**, treated as *maintained, versioned data* and *proven on a held-out set*.

**Mechanism (decide before build):**
1. **Primary — a versioned `procedural-intent → unit-slug` map as DATA** (registration hold→registrar, transcript→registrar, tuition/refund→bursar, I-20/OPT→ogi, …). It is deterministic and defensible, BUT it is explicitly *data that is grown*, not a code keyword hack, and it is **eligible to resolve to department/college units too** (advising pages legitimately answer procedural queries), not only `type='office'`.
2. **Alternative to A/B — a soft `org_kind`/unit prior fed into the cross-encoder reranker** (learned relevance) rather than a hard scope (RAG's best-practice note: structural = routing + metadata filter + learned reranking together). A/B this against (1).
3. **If any multiplicative prior survives**, it is a **bounded tiebreaker only**: pool-only (never injects), capped so it can **never override a top-quartile CE score**.

**Proof gate — this is what makes G2 real, not relocated [RAG I1 / SE MEDIUM-3]:** a **HELD-OUT office-intent eval set whose cases were NOT used to build the map/classifier.** `test_office_routing_gold` (top-1 correct office/unit) is the in-sample gate; the held-out set is the anti-memorization gate. Passing only the in-sample set proves nothing about the long tail (the exact failure mode the spec criticizes).

### Grids / structured facts off the vector path [ARCH R6]
- Grids (enumerative tables — rosters, Dean's List, schedules) → FTS + existing deterministic SQL skills + **one summary vector** for discovery; never N near-identical row-vectors in KNN.
- **Detector precision is the whole ballgame [ARCH R6]:** the DB has **768 served items with >40 lines** but only ~60 true grids. Build a **labeled grid/not-grid set from those 768 first**; pick a **high-precision threshold favoring false-negatives** (a mis-chunked grid is recoverable via SQL skills; a mis-classified prose page is silently lost from semantic recall). Row self-similarity is cheaply computable at ingest (token-Jaccard on sampled lines / compression ratio). Both directions go into eval.

---

## 8. Migration / cutover (data-safety) [G5 / DATA]

**Decision: NOT a greenfield wipe, and NOT a node/edge wipe either. Reconcile-in-place regeneration on a COPY of the live file.** A `create_all`-style fresh DB would (a) drop 10 live tables not in `schema.py` (`questions` 920, `response_feedback` 92, `admin_actions`, `jobs`, `events_log`, …), (b) renumber `organizations.id` — a plain insert-order rowid — dangling every immortal `posts.org_id` FK **[DATA R1, catastrophic]**, and (c) lose all DB-only data with no seed.

**The same catastrophe recurs at `nodes.id → edges` and was missed in the first draft [SE HIGH-1, proven on the live DB]:** all **211 Scholar-metric Person nodes are `source='crawler'`** — the Scholar metrics live in `attrs.profiles.scholar` *on the crawler node itself*, i.e. ON the would-be wipe target (678 crawler Person nodes carry a profiles bag). And **637 `source='scholar'` edges + 14 `source='njit-crawl'` edges reference crawler nodes** via `edges.src_id/dst_id REFERENCES nodes(id)` with no `ON DELETE` (→ RESTRICT). A `DELETE FROM nodes WHERE source='crawler'` therefore **fails with a FK IntegrityError**, and a delete+recreate would renumber node rowids and sever all 651 preserved edges + all 211 metrics from their nodes. **So crawler graph data is NOT wiped — it is refreshed by the idempotent crawl's upsert-in-place-by-`key`** (`store.py`/`explore.py` already operate this way: rowid-stable) **+ `reconcile_departures`.** This preserves node rowids, the 651 cross-referencing edges, and the 211 metrics in place, untouched. The source taxonomy must explicitly handle `njit-crawl` (15 people) and `scholar` (380 nodes / 637 edges) as **cross-referencing layers on crawler nodes**, not as independent wipe/keep buckets.

### Preservation matrix (live DB, queried)
| Class | Data | Action |
|---|---|---|
| **Preserve verbatim** | `posts` 329, `post_deliveries` 874 (immortal), `judging_*`, `events`, `post_templates`, the 10 non-schema analytics/audit tables | carried in the copy, untouched, **ids intact** |
| **Refresh in place (crawler-sourced)** | crawler people/graph (1,068 Person + edges), college_crawl prose (1,376), aliases, crawl cache | **idempotent re-crawl upsert-by-`key` (rowid-stable) + `reconcile_departures`** — NOT delete+recreate; chunk/vector tables rebuilt additively |
| **Cross-references ON crawler nodes (survive because nodes are rowid-stable)** | **211 Scholar metrics** (`attrs.profiles.scholar` on `source='crawler'` Person nodes), 637 `scholar` + 14 `njit-crawl` edges, 380 scholar ResearchArea nodes, 184 scholar KB | preserved by in-place upsert (`merge=True` already protects manual metrics); **NOT wiped** |
| **Preserve-in-place (DB-only, NO seed file)** | **41 of 47 manual people** (NJIT cabinet ~21, club officers ~13, theater ~10), **30 of 31 settings**, ~110 dashboard/migration KB, manual profile links | kept untouched; exported to a file as backup before any change anyway |

Because we keep all non-crawler data in place, **GSA + MMI are preserved automatically** — no manual re-add. **MMI = "Multimedia Intelligence Workshop" (mmiseries.org): org `id=3` slug `mmi` type `event_series` + 30 FAQ rows ids 44–73, tagged `created_by='migration'` (NOT `dashboard`).** This is exactly why the wipe MUST be a **positive allowlist** (`created_by IN (crawler, college_crawl)` only) and NOT a denylist / "keep only dashboard" — the latter would silently destroy MMI's 30 migration-tagged rows. MMI has a real website, so it is a **future crawl candidate** (unlike GSA's Wix); manual for now.

### Safe cutover sequence
- **Phase 0 — capture truth (before touching anything):** `hardened_backup` + copy the pre-wipe snapshot to an **out-of-rotation, checksummed** path (backups rotate to newest-10 and a multi-day rebuild could evict it **[DATA R10]**). Record a **manifest**: per-`created_by` KB counts, per-`source` node/edge counts, people count, **org `slug→id` map**, posts/deliveries/judging counts, `attrs.profiles.scholar` dump, settings dump, non-schema table row counts. Export the DB-only re-seed sets to in-repo files.
- **Phase 1 — build offline on a COPY (reconcile-in-place, no graph wipe):** start from a copy of the live file (so immortal + unknown tables + all ids survive). Then: (i) refresh KB content via the source-scoped reconcile (`created_by` crawler/college_crawl items version-bump in place — soft-delete, never a hard wipe of nodes/edges); (ii) **re-run the idempotent crawl upsert-by-`key` + `reconcile_departures`** to refresh people/graph rowid-stably — Scholar metrics/edges on those nodes ride along untouched (`merge=True`); (iii) clear only the regenerable crawl CACHE (`raw_pages`/`frontier`/`page_nodes` — confirm no preserved FK references them); (iv) build the additive chunk + chunk-vector tables and **batched** `embed_all` (chunked); (v) `apply_org_aliases`; (vi) `verify_kg`/`verify_gsa`. `entity_id` derivation FROZEN throughout (§5) so re-applied/again-present metrics key to the same nodes.
- **Phase 2 — acceptance gate, fail-closed, WITH PROOF** (honors evidence-before-claim hard line): immortal table row counts **byte-identical** vs snapshot; every `org_id` in posts/settings/events resolves to the **same slug** as pre-change; people ≥ baseline; `source='dashboard'` nodes ≥ baseline; **0 dangling edges** (`edges.src_id/dst_id` all resolve) **[SE HIGH-1]**; **node-id stability** for every `scholar`/`njit-crawl`-referenced node (same rowid as snapshot); **Scholar-metric people == 211 keyed by `entity_id`** (0 orphaned profile keys); settings == 31; all non-schema tables present ≥ baseline; embed coverage == active KB count; `verify_kg`/`verify_gsa` clean; **0 orphan chunk-vectors** (the invariant test, run as a gate).
- **Phase 3 — atomic swap + rollback:** stop bots → single `mv` of the verified copy over `gsa_gateway.db` → `restart.sh`. Rollback = `mv` the out-of-rotation snapshot back + restart (trivial; original never mutated in place).

---

## 9. The one fenced bet — Contextual Retrieval (OPTIONAL)

Per-chunk context blurb via **local llama3.1:8b** before embed+FTS (Anthropic technique, run locally → not a lock-in). Strictly fenced **[ARCH R5]**:
- **Off by default**; enabled only after an `eval.sh` A/B shows a measured win.
- **`model_id` + sampler params folded into the chunk `content_hash`** — else a model/driver change silently diverges blurbs without changing the hash, and hash-skip refuses to refresh → stale vectors.
- **Reindex cost acknowledged as release-blocking:** ~9,758 serial local generations ≈ hours, against the same Ollama the live bot uses. Not a footnote.
- **The published gains do NOT transfer [RAG I6]:** Anthropic's −35/49/67% figures used Claude + prompt caching; on serial local llama3.1:8b there is no caching AND lower blurb quality, so the *win* (not just the cost) is unproven. The A/B must clear a **local-model quality bar measured on our eval**, not Anthropic's numbers.
- Defensible vs mechanical-clean: index-time enrichment never served to the user; but it shapes what gets served, so it stays fenced and eval-gated.
- Nothing structural depends on it; drop freely if the pilot doesn't pay.

---

## 10. Testing & eval [G1–G5 proof]

- **Long-doc deep-recall set** — questions whose answer provably lies **past char 2000** in known long items (mine the 1,055). Metric: hit@5 + answer-contains. **G1 proof** (baseline poor → post-fix jumps).
- **Office-routing gold** (`test_office_routing_gold`, expanded) — top-1 correct office/unit. Hard gate ≥ baseline. **G2 proof** + catches over/under-firing.
- **Short-item regression** — existing `eval/questions.txt` via `eval.sh` auto-judge. Hard gate: within ±1–2%.
- **Grid set** — roster/schedule/advisor/deadline lookups. Hard gate ≥ baseline (both detector directions).
- **Distinct-parent gate** — assert ≥ `pool_size` distinct parents recovered on grid-heavy queries **[ARCH R1]**.
- **Org-filtered deep retrieval** — a known deep-in-corpus org page is retrievable under its org filter **[ARCH R3]**.
- recall@k / nDCG@10 on labeled sets; latency p50/p95; index-size delta (confirm ~1.6× served).
- **A/B flag** for collapse + CE-on-chunk; ship only on a measured win **[ARCH R1/R4 — reject criteria]**.
- Every probe question added to `eval/questions.txt` (suite only grows — `feedback_grow_correctness_suite`).

---

## 11. Goals checklist (shipped / deferred — updated at Plan 3 review, 2026-06-26)

| Goal | Mechanism | Status |
|---|---|---|
| G1 truncation killed | §6 chunking + chunk-KNN retrieval; CE on the matched chunk | ✅ **BUILT + PROVEN** behind `RETRIEVAL_CHUNKS` (off). Paraphrased deep-content hit@5 **3/30 → 11/30** (3.7×); verbatim 10→11/25; distinct-parent gate + dynamic over-fetch shipped. The 4 legacy `[:2000]` removals happen at Plan-4 cutover (chunk path supersedes; flagged, not silent). |
| G2 office dilution (structural) | §7 mechanism-1 candidate-set scoping + held-out eval gate; prior only as capped tiebreaker | 🟡 **DEFERRED — NOT shipped as designed.** Built only mechanism-3 (a multiplier), and it is UNCAPPED (can override a strong CE rank — reject #3) and proven only in-sample (reject #6 unmet). A capped tiebreaker mathematically can't fix routing when the right page has a weak CE score, so the correct fix is the spec's mechanism-1 (deterministic candidate-set SCOPING) + a genuinely held-out set. Prior kept OFF by default; G2 redesign is its own focused follow-up. **Owner scope decision at Plan 4: ship G1 now, do G2 separately.** |
| G3 LLM-agnostic | §6 model descriptor, provider-isolated embed | ✅ BUILT (model_descriptor.py; embed_fn injected). |
| G3 max-capacity (index+query) | §6 working_size + §7 in-engine `k=?` + org partition pushdown | ✅ BUILT on chunk path (reject #2 met: in-engine partition KNN). Legacy `_semantic` fetch-then-filter retired at cutover. |
| G4 one ingestion engine | §5 (with all `explore.py` invariants ported) | ⏳ Plan 5 (last, separate — not on the retrieval critical path). |
| G5 rebuild w/o data loss | §8 reconcile-in-place (NO node/edge wipe) + fail-closed manifest-diff gate | ⏳ Plan 4 (owner cutover gate). GC sweep proven (891 orphans on a copy). |
| Contextual Retrieval | §9 | OPTIONAL / fenced — not built, off by default (correct). |

**Plan 3 reject-criteria status (2026-06-26):** #1 eval.sh A/B — running (baseline vs chunks-on, prior off). #2 in-engine KNN — ✅ met on chunk path. #3 bare multiplier — addressed by DEFERRING the prior (not shipped on). #4 Contextual fenced — ✅. #6 held-out office set — folded into the G2 redesign (prior off until then). Both hard-gate reviews (senior-eng + RAG) folded; chunk path passed.

## 12. Reject criteria (from the pressure-tests)
1. No A/B proving chunk-collapse + CE-on-chunk ≥ current ranking on `eval.sh`.
2. `_VEC_KNN_MAX` left as fetch-then-filter.
3. Office dilution "fixed" only by a keyword×category multiplier.
4. Contextual Retrieval not fully fenced (off-default, model-id in hash, reindex cost owned).
5. Any greenfield wipe **or any delete+recreate of `nodes`/`edges`/`organizations`** (rowid renumber = dangled FKs + orphaned Scholar metrics — must be reconcile-in-place by `key`), or any cutover without the fail-closed manifest-diff gate.
6. G2 proven only on the in-sample `test_office_routing_gold` with no held-out office-intent set.

## 13. Open questions for owner
- ~~**MMI** identity~~ — RESOLVED: Multimedia Intelligence Workshop (mmiseries.org), org id 3 + 30 `migration`-tagged FAQ rows; preserved (kept untouched); future crawl candidate.

## 14. Build order (5 flag-gated plans, retrieval proven BEFORE cutover) [SE]
1. **Schema + orphan/GC fix (additive, no behavior change).** Add `knowledge_chunks` + chunk-vector table (with `org_id`/`type`/`parent_id` columns) + the `is_active`-keyed GC sweep + invariant test. **Ship the GC sweep against the CURRENT DB first — it fixes the existing 891-orphan bug standalone** and proves the invariant. Lowest risk, immediate value.
2. **C — descriptor + chunker + invalidation wired into the enumerated writers.** Populate chunks on a copy. No serving change.
3. **D — chunk-KNN → collapse → RRF → rerank → hydrate, behind an A/B flag**, with the metadata-column filter-pushdown. Prove G1 (deep-recall), G2 (held-out office-intent + gold), short-item regression on `eval.sh` **before** flipping. Reject criteria #1/#2/#3/#6 apply here.
4. **G5 cutover** — reconcile-in-place (§8) + fail-closed acceptance gate — **only after step 3 proves a win** (else you re-embed twice).
5. **G4 engine unification — separate, LAST.** Pure producer-side; the 7 `explore.py` invariants are the regression surface; not a dependency of G1/G2/G3/G5, so it must not gate the retrieval wins. Reviewed by senior-eng as an ingestion-correctness concern.

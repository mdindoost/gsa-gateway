# Cross-Encoder Reranking for the v2 Retriever — Design

**Date:** 2026-06-16
**Status:** Approved + senior-eng review incorporated (C1 gate→chunk-level, C2 boost×CE,
S2 full-pool rerank + recall diagnostic, S3/S4 tokenizer & I/O, N1-N5); ready to plan
**Author:** session work with Mohammad
**Relates to:** `project_retrieval_architecture`, `project_golden_eval_harness`

## Problem & Evidence

A 100-question end-to-end eval (`scripts/_eval_kb_100.py`, run through the real
`message_handler` pipeline) scored **78/100** — KB-grounded **37/50 (74%)**, cold
"any student" **41/50 (82%)**. Sorting the 13 KB-grounded misses by mechanism:

- **~9–11 are "right document, WRONG chunk"** — the answer exists in the KB but RRF
  fusion ranked a topically-adjacent chunk above the one containing the asked fact, so the
  LLM answered "not specified" or confabulated. Examples: *who chairs the General Assembly*
  (got the meeting-schedule chunk, not the VPAA duty), *conference grant amount* (got the
  $150 asset-grant chunk, not $500), *AirBNB reimbursable* (got the constitution travel
  budget, not the packet's explicit exclusion), *term limits*, *advisors' offices*,
  *expense-report 30-day deadline*.
- ~2 are missing structured skills (e.g. "list the grad clubs" — the KG has them).

**Root cause for the dominant bucket:** dense vectors + bm25 capture *topical* similarity,
not *the specific attribute/relation asked*. For "the X of Y" lookups, nearest-neighbor on
the whole question reliably lands on a neighbor. A reranker that scores each candidate
*jointly with the query* (cross-encoder) is the standard, genuine fix — not chunk boosts or
per-fact keyword hacks.

This increment addresses **only** the reranking lever, measured in isolation against the
harness. Chunking changes (Gap A), a grounding/answerability gate (Gap C), and structured
enumeration/policy skills (Gap B-structured) are explicitly **out of scope** here and tracked
as separate future increments.

## Approach (decided during brainstorming)

- **Lever:** add a reranker over the existing hybrid top-K (chosen over structured-skills and
  a grounding-gate as the highest measured leverage).
- **Mechanism:** an **ONNX cross-encoder** (`Xenova/ms-marco-MiniLM-L-6-v2`, ~90MB) run via
  the already-installed `onnxruntime` + `tokenizers` (no `torch`/`sentence_transformers`).
  Chosen over an LLM reranker (local 8B is a weak judge; +1–3s latency) and a lexical
  reranker (heuristic, closer to a band-aid).
- **Provisioning:** auto-download from HuggingFace into a gitignored `models/reranker/` on
  first use and cache; pre-warmable via a script / Jobs-tab action. One outbound HF fetch on
  first run only; no personal data sent.

## Architecture & Data Flow

One seam inside `V2Retriever.retrieve()`, between RRF fusion and the `limit` slice. The rest
of the pipeline is unchanged.

```
query → embed (nomic) ─┐
      → FTS bm25 ───────┤→ RRF fuse + type-boost → ranked pool (~N)
                        ▼
              CrossEncoderReranker: score (query, chunk.content) for top-N,
              reorder those N by CE score (RRF tiebreak); tail untouched
                        ▼
        _diversify_and_expand → top `limit` → Ollama generation   (unchanged)
```

- Runs **only on the semantic-RAG path**. Structured routes (`router.py` → `skills.py`)
  bypass retrieval and are untouched.
- The cross-encoder reorders the fused `ranked` pool, but the existing **type-boost prior is
  preserved, not discarded** (senior review C2): a pure CE reorder would overwrite the
  `event_info` boost for any item in the reranked window — and event-type questions (MMI,
  3MRP) are in the eval set. Resolution: `final_score = sigmoid(CE_logit) × type_boost`, then
  sort. CE drives ordering; the boost survives as a multiplicative prior (same shape as
  today's boost). Sigmoid normalizes CE to (0,1) so the multiply is well-behaved.
- **Reorder the full fused pool, not a narrow window** (senior review S2): set the rerank
  width to the full pool (`>= pool_size`, default 40), not 20. A cross-encoder cannot rescue
  a gold chunk that sits below the reranked window, and several misses (AirBNB exclusion,
  30-day deadline) may be borderline-recall. The CPU cost of 40 short pairs (~40-80 ms) is
  negligible. **Before building, instrument the gold-chunk *fused rank*** for each target
  miss; if any gold chunk falls outside `pool_size`, widening recall (`pool_size`) joins this
  increment rather than a later one.
- After reordering, hand off to the existing `_diversify_and_expand` (entity round-robin),
  unchanged — but validate it on a multi-entity question (e.g. "the GSA's two advisors",
  in the eval) so CE clustering several chunks of one entity doesn't starve diversity
  (senior review S1).

## Components

### 1. `v2/core/retrieval/reranker.py` — `CrossEncoderReranker`
- Lazy singleton (mirrors the embedder). Loads `model.onnx` + `tokenizer.json` from
  `models/reranker/`; if absent, `huggingface_hub.snapshot_download(repo, allow_patterns=
  [onnx + tokenizer files])` into that dir, then loads.
- `score(query: str, passages: list[str]) -> list[float] | None`: tokenize (query, passage)
  pairs, batch through `onnxruntime` → relevance scores. Returns `None` to signal "fall back".
- **Tokenizer specifics (senior review S3 — `tokenizers` alone, no `transformers`):**
  explicitly `enable_truncation(max_length=512, truncation_side="right")` and
  `enable_padding(...)` (the saved `tokenizer.json` may not carry these active); pair-encode
  via `encode(query, passage)` so `[SEP]` and `token_type_ids` are correct. **Truncate on the
  passage side only** — never the query — but verify long policy chunks don't lose the trailing
  fact (our chunks are <=350 tokens, so 512 comfortably fits the pair; assert this).
- **Model I/O (senior review S4):** at load, read `session.get_inputs()` to learn the exact
  inputs (`input_ids`, `attention_mask`, and **whether `token_type_ids` exists** — MiniLM
  cross-encoders use it; omitting a required one errors, passing zeros silently degrades
  ranking). Read output shape: handle `[batch,1]`/`[batch]` (squeeze) and the 2-class
  `[batch,2]` head (`logits[:,1]`) — a blind sigmoid of column 0 would invert ranking.
  Sigmoid is order-preserving so it's only for readable trace scores, not correctness.
- **Lazy load is lock-guarded** (senior review N1): the bot runs `retrieve()` in
  `asyncio.to_thread` with concurrency up to 4; guard the one-time `snapshot_download` +
  `InferenceSession` construction with a lock so concurrent first-queries don't double-load.
  Pin `intra_op_num_threads` modestly (shared box also running Ollama).
- `warm()`: optional eager load at bot startup. `available` flag set false after a load
  failure so we don't retry every query.

### 2. Integration in `V2Retriever`
- New constructor arg `reranker=None` (optional; `None` = current behavior, keeps all existing
  call sites working).
- In `retrieve()`, after `ranked = sorted(scores...)` and `rows` hydration: if rerank is
  enabled and a reranker is present, take the top `rerank_pool` ids, score
  `(query, rows[id]["content"])`, and reorder those ids by descending CE score (stable on the
  prior order for ties). Replace that prefix of `ranked`; continue unchanged.

### 3. Settings (admin-tunable, same pattern as `_load_boost` / `exclude_types`)
- `retriever.rerank_enabled` — default `true` once shipped (instant kill-switch). **Parse as
  bool**, not via `_load_boost` (which expects a float and would choke on "true") — add a
  small `_load_bool` helper (senior review N4). Read per-construction, like the existing
  settings, so the kill-switch is instant.
- `retriever.rerank_pool` — default `= pool_size` (40), i.e. the full fused pool (S2), not 20.
- `retriever.rerank_model` — default the HF repo id.

### 4. Provisioning & wiring
- **`models/reranker/`** added to `.gitignore` (scoped, not bare `models/` — N3).
- `scripts/fetch_reranker.py` for explicit pre-warm; **lazy auto-download on first use** is
  the primary path. (Dropped the separate Jobs-tab button — three provisioning paths for one
  90MB fetch is over-engineered, senior review N5.)
- The `CrossEncoderReranker` is a **shared, module-level singleton** held by the shim and
  passed into each per-call `V2Retriever(conn, embedder, reranker)` — the shim constructs a
  fresh retriever per query (line ~73) but must NOT recreate the reranker (would reload the
  model) (senior review N2). `warm()` at startup, non-blocking; failure is non-fatal.

## Error Handling & Fallback (strictly additive — never breaks an answer)

| Failure | Behavior |
|---|---|
| Model absent + offline (can't fetch, not cached) | log once, set `available=false`, disable for session → RRF order |
| Per-query tokenizer/onnx exception | caught, fall back to that query's RRF order, debug-logged |
| `rerank_enabled=false` | skip entirely |
| Empty pool / single candidate | no-op |

`_write_trace` gains a `reranked: yes/no` line + per-candidate CE scores for debuggability.

## Testing & Acceptance Gate

**Unit (`reranker.py`):**
- Toy ordering: `score("who chairs the GA", [<VPAA-duties>, <meeting-schedule>])` → VPAA first.
- Fallback: empty/missing model dir + offline → `score()` returns `None`, raises nothing.

**The objective gate is deterministic and chunk-level, NOT answer-text** (senior review C1).
The harness only writes free-text LLM answers (no scorer), and llama3.1 generation is
nondeterministic — so "zero regressions" cannot be asserted by re-judging answers. Instead:

**Primary gate — a new `tests/test_rerank_gold_chunks.py` (deterministic, CI-able):**
- A frozen table of the ~13 KB target questions → the **gold `knowledge_items.id`** that
  contains the asked fact (curated once from the current DB).
- For each, call `retrieve()` and assert the gold id is at **top-1** (or within top-`limit`).
  Run **rerank ON vs OFF**: gold-in-top-1 must rise materially with rerank and **must not
  regress** on any question that already passed without it.
- A second frozen list of ~10 "already-correct" KB questions → their gold ids must **stay**
  in top-`limit` with rerank on (guards the 0-regression requirement deterministically).
- No LLM in this gate → reproducible, fast, mergeable.

**Pre-build diagnostic (run first, informs S2):** for every target question, log the gold
chunk's *fused rank* (pre-rerank). Confirms each miss is a *ranking* failure (gold in pool,
rank>1) the reranker can fix vs a *recall* failure (gold outside `pool_size`) needing wider
recall. Drives whether `pool_size` widening is in-scope.

**Secondary smoke (not the merge bar) — `scripts/_eval_kb_100.py`:** re-run end-to-end for a
sanity read; expect KB-grounded to climb toward ~88% and the cold set to hold ~82%, judged by
human read. Used to catch surprises, not as the deterministic gate.

If the chunk-level gate isn't cleared, tune `pool_size`/`rerank_pool`/model **before** merge.

## Latency

~20–40 ms/query for 20 short pairs on CPU; model load once (~few hundred ms) lazily.
Acceptable for the interactive chat path. Reranking is skipped for structured routes (no cost
there).

## Out of Scope (separate future increments)
- Structure-aware chunking of policy docs (Gap A).
- Grounding/answerability gate to decline instead of confabulating (Gap C).
- Structured enumeration/policy skills — "list clubs", policy-fact routing (Gap B-structured).

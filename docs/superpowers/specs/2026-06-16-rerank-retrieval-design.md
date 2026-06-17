# Cross-Encoder Reranking for the v2 Retriever — Design

**Date:** 2026-06-16
**Status:** Approved (design); pending senior-eng review before build
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
- The cross-encoder is the **authority on ordering the shortlist**: reorder the top-N of the
  fused `ranked` pool by CE score; leave the tail as-is; hand off to the existing
  `_diversify_and_expand`. **No score-blending** (avoids fragile weights). The existing
  type-boost prior is applied before reranking, as today.

## Components

### 1. `v2/core/retrieval/reranker.py` — `CrossEncoderReranker`
- Lazy singleton (mirrors the embedder). Loads `model.onnx` + `tokenizer.json` from
  `models/reranker/`; if absent, `huggingface_hub.snapshot_download(repo, allow_patterns=
  [onnx + tokenizer files])` into that dir, then loads.
- `score(query: str, passages: list[str]) -> list[float] | None`: tokenize (query, passage)
  pairs (padded/truncated to model max, e.g. 512), batch through `onnxruntime`, sigmoid the
  logits → relevance scores. Returns `None` to signal "fall back" (model unavailable).
- `warm()`: optional eager load at bot startup.
- `available` flag, set false after a load failure so we don't retry every query.

### 2. Integration in `V2Retriever`
- New constructor arg `reranker=None` (optional; `None` = current behavior, keeps all existing
  call sites working).
- In `retrieve()`, after `ranked = sorted(scores...)` and `rows` hydration: if rerank is
  enabled and a reranker is present, take the top `rerank_pool` ids, score
  `(query, rows[id]["content"])`, and reorder those ids by descending CE score (stable on the
  prior order for ties). Replace that prefix of `ranked`; continue unchanged.

### 3. Settings (admin-tunable, same pattern as `_load_boost` / `exclude_types`)
- `retriever.rerank_enabled` — default `true` once shipped (instant kill-switch).
- `retriever.rerank_pool` — default `20` (candidates reranked).
- `retriever.rerank_model` — default the HF repo id.

### 4. Provisioning & wiring
- `models/` added to `.gitignore`.
- `scripts/fetch_reranker.py` (optional pre-warm) + a Jobs-tab "pre-warm reranker" action.
- Wire a shared `CrossEncoderReranker` into the assistant/`retriever_shim` so the bot path
  uses it; `warm()` at startup (non-blocking; failure is non-fatal).

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

**Integration (`retriever.py`, rerank on vs off):** assert the specific eval-failing chunks
surface in top-1/top-3 — AirBNB-not-eligible, conference-grant-$500, "VPAA chairs GA",
term-limits, advisors' offices.

**The objective gate = the eval harness** (`scripts/_eval_kb_100.py`):

| Metric | Before | Target |
|---|---|---|
| KB-grounded accuracy | 74% (37/50) | **≥ 88%** (recover ≥7 of the ~11 wrong-chunk misses) |
| Regressions vs the 37 already-correct | — | **0** |
| Cold set (mostly out-of-scope) | 82% | **≥ 82%** (no regression) |

If the bar isn't cleared, tune `rerank_pool` / model **before** merge.

## Latency

~20–40 ms/query for 20 short pairs on CPU; model load once (~few hundred ms) lazily.
Acceptable for the interactive chat path. Reranking is skipped for structured routes (no cost
there).

## Out of Scope (separate future increments)
- Structure-aware chunking of policy docs (Gap A).
- Grounding/answerability gate to decline instead of confabulating (Gap C).
- Structured enumeration/policy skills — "list clubs", policy-fact routing (Gap B-structured).

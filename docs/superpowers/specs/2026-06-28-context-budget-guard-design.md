# Context-Budget Guard — design

**Date:** 2026-06-28
**Branch:** `feat/context-budget-guard` (off `main` @ `69fcfd6`)
**Status:** design — awaiting owner review → expert reviews (senior-eng + RAG) → owner sign-off → TDD build
**Arc:** Phase-3 Task #1 of the teacher-eval / answer-stack arc ([[project_answer_stack_design]],
[[project_m2_embedding]], [[project_teacher_eval]]). Corpus-independent; ships to prod independently of
the gate refit / DB-rebuild (Decision A = hybrid; Decision B = fresh branch off main).

---

## 1. Problem

Live prod bug, confirmed 2026-06-27: the query **"what is the H-1B cap gap period for F-1 students"**
returns just **"The"** (Source: Office of Global Initiatives).

Root cause (proven read-only end-to-end, re-verified in current code 2026-06-28):

- `bot/services/ollama_client.py` runs generation with `num_ctx=8192`.
- `_build_context_block` (line 165) packs the **full `.text` of every retrieved chunk** the caller
  passes (top-k from the retriever, k≈5) with **no token budgeting**. Its comment ("each chunk is now a
  single focused item … no bloated document to truncate") is false for long crawled policy/immigration
  pages.
- For the cap-gap query the top-5 OGI pages total ~37,273 chars ≈ 9,300 tokens; the full prompt
  (system + docs + history + answer) is ~10–11k tokens, well over 8,192.
- Ollama **silently truncates the front** of the prompt on overflow (the code comment at lines 149–154
  documents this) → the model receives mangled context → llama3.1:8b degenerates to "The".
- Proven: top-chunk-only (2,949 tok, fits) → correct full answer; overflow (~12.9k tok) →
  `prompt_eval_count` capped at 8191 → output "The".

This is not one query — it breaks on exactly the richest long policy/immigration pages (OGI, financial
aid, …). It is the generation-layer face of the M2 long-page problem.

**Why chunking does not fix it:** the planned Modified-A / Phase-2 deep-fallback returns the **full parent
page to the LLM** (never-withhold). Handing back a 12k-char parent recreates the same overflow. A
context-budget invariant is required **regardless of chunking**, and is a **prerequisite** before enabling
either the long-page path or Phase-2's deep-fallback flag.

## 2. Goal & non-goals

**Goal:** the assembled generation prompt can **never** silently overflow the context window. Any
trimming is **explicit and budgeted**, never Ollama's silent front-truncation. The canonical cap-gap
query (and the long-page class it represents) answers correctly, never "The".

**Non-goals (explicitly out of scope):**
- No retrieval changes (ranking, fusion, CE, chunk logic) — pure generation-layer plumbing.
- No gate refit / abstain logic (that is the later Phase-3 cutover, folded into the rebuild).
- No chunking / `model_descriptor` / matched-chunk machinery (lives on the unmerged gate branch).
- No merge of any unmerged branch; this ships standalone off main.

## 3. Decisions locked with owner (2026-06-28)

- **D1 — `num_ctx` 8192 → 16384** + budget guard as backstop. Hardware verified: RTX 4070 Ti SUPER, 16 GB,
  loaded llama3.1:8b @ 16k ≈ ~8.5–9 GB → ~7 GB free. The typical long bundle (~9.3k tok) then fits whole;
  the guard only trims rare monsters (e.g. 108KB grids). Honors use-max-capacity + prefer-verbatim.
- **D2 — Budget is DERIVED, never hardcoded.** `doc_budget = num_ctx − tokens(system) − tokens(history) −
  num_predict − safety_margin`. Tracks any change to `num_ctx`/`num_predict`. LLM-agnostic by construction.
- **D3 — Token counting = conservative char-based estimate** (`tokens ≈ ceil(chars / CHARS_PER_TOKEN)`,
  `CHARS_PER_TOKEN = 3.5`). On main there is no `count_tokens` helper and no llama tokenizer; a conservative
  ratio **over-estimates** token count → **under-fills** → cannot overflow, and works for any generation
  model (most LLM-agnostic). Accepted cost: a little headroom left unused. Flagged for RAG/senior-eng review.
- **D4 — Truncation = verbatim prefix** (matched-chunk truncation deferred, see §7). Truncating an
  over-budget page to its verbatim prefix preserves the canonical cap-gap answer (its first sentence) and
  honors crawl=mechanical / verbatim. Matched-chunk-aware truncation folds in with the chunk work at the
  rebuild.
- **D5 — Never-withhold via link preservation.** Every page's `Source:` line is kept in the prompt even
  when its body is trimmed or dropped, so the full content is one click away — the generalization of the
  owner-approved "matched section + link" grid exception to "as-much-verbatim-as-fits + link".
- **D6 — Cutover = merge to main + restart on sign-off.** This piece lands on prod at the end of its own
  hard-gate cycle (live bug fix, corpus-independent). The gate refit / matched-chunk refinement go to main
  later via the rebuild's own cutover.

## 4. Architecture

One budgeting helper inside `bot/services/ollama_client.py`, applied to the chunk list **before**
`_build_context_block`, used by both generation entry points.

```
generate_answer / compose_from_rows
        │
        ▼
  _fit_chunks_to_budget(chunks, reserved_tokens)   ← NEW pure function
        │   • compute doc_budget = num_ctx − reserved − num_predict − margin
        │   • walk chunks in relevance_score order
        │   • add whole pages while they fit
        │   • fill remaining budget with verbatim prefix of the boundary page
        │   • drop the rest (links still surfaced — see below)
        ▼
  _build_context_block(fitted_chunks)              ← unchanged body
```

Rejected alternatives:
- Guard at retriever/caller layer → duplicates budget logic across callers, drifts. Rejected.
- num_ctx raise alone, no guard → does not scale to 108KB grids; silent truncation returns on the next
  bigger page. Rejected (owner already chose guard-as-backstop).

## 5. Components

### 5.1 `OllamaClient.__init__`
`num_ctx` default 8192 → **16384**. (Single constant; both call paths already read `self.num_ctx`.)

### 5.2 `_estimate_tokens(text: str) -> int` (NEW, module-level pure fn)
`return ceil(len(text) / CHARS_PER_TOKEN)` with `CHARS_PER_TOKEN = 3.5`. Conservative on purpose.
Module constant so reviewers/tuning have one knob.

### 5.3 `_fit_chunks_to_budget(chunks, reserved_tokens, num_ctx, num_predict) -> (list[RetrievedChunk], list[str])` (NEW, pure fn)
Returns `(fitted_chunks, dropped_urls)` — the second list feeds the ADDITIONAL SOURCES block (§5.4).
- `doc_budget = num_ctx − reserved_tokens − num_predict − SAFETY_MARGIN` where:
  - `reserved_tokens` = estimated tokens of EVERYTHING in the prompt that is **not** the per-chunk document
    bodies: the full system prompt (which already includes conversation history — see `_build_full_prompt`),
    the student question, and the fixed instruction text of the user prompt. Computed by the caller and
    passed in, because the fit must happen **before** the context block is built (§5.5).
  - `SAFETY_MARGIN` (module constant, e.g. 384) — absorbs the per-chunk framing `_build_context_block`
    adds (labels, `Section:`, `Source:`, `[Relevance]`, delimiters) × ~k chunks, plus char-estimate slack.
- Sort by `relevance_score` desc (stable; chunks usually arrive already ranked — sort defensively).
- Greedy pack: for each chunk, if `_estimate_tokens(chunk.text)` ≤ remaining budget, include whole;
  else include a **verbatim prefix** sized to the remaining budget (chars = `remaining_tokens *
  CHARS_PER_TOKEN`, cut on a whitespace boundary ≤ that length to avoid mid-word splits), mark it
  truncated, and stop.
- If **the first chunk alone** exceeds budget → include its verbatim prefix (rank-1 is never dropped).
- A truncated chunk is returned as a shallow copy with truncated `.text` (originals never mutated).
- Returns `(fitted_chunks, dropped_urls)`; `fitted_chunks` has ≥1 chunk whenever input non-empty and
  budget > 0. `dropped_urls` = `source_url` of every chunk excluded entirely (deduped, order preserved).
- **Degenerate budget** (≤ 0 from pathological history): return `([rank-1 prefix], dropped_urls)` so we
  always send something grounded; log a warning.

### 5.3a `_fit_text_to_budget(text, reserved_tokens, num_ctx, num_predict) -> str` (NEW, pure fn)
Sibling for `compose_from_rows`, whose payload is a single `facts` string, not a chunk list. Same budget
formula; if `_estimate_tokens(text)` exceeds budget, return a whitespace-snapped verbatim prefix with an
explicit `\n…(list continues)` marker appended. No silent overflow.

### 5.4 Link preservation (never-withhold)
`_build_context_block` already emits the `Source:` line per chunk when `source_url` is present — preserved
unchanged for included/truncated chunks. For a **dropped** chunk that has a `source_url`, append a compact
`=== ADDITIONAL SOURCES (not shown in full) ===` block listing those URLs, so the link survives even when
the body was budget-dropped. (Implemented in `_build_context_block`, fed the dropped-chunk URLs from the
fit step; keeps the never-withhold line honest without spending body budget.)

### 5.5 Wiring
`_build_full_prompt` is refactored so the system prompt (incl. history) and the fixed user-prompt framing
(question + instruction text) are computed **first** — they don't depend on chunks — then the chunks are
fitted, then the context block + final user prompt are assembled from the fitted chunks. Ordering:
1. Build `system_prompt` (BASE + history) and the constant instruction/question framing of the user prompt.
2. `reserved = _estimate_tokens(system_prompt) + _estimate_tokens(question) + _estimate_tokens(INSTRUCTIONS)`.
3. `fitted, dropped_urls = _fit_chunks_to_budget(chunks, reserved, self.num_ctx, num_predict=512)`.
4. `context_block = _build_context_block(fitted, dropped_urls)` → user prompt → payload.
- `generate_answer`: as above (num_predict=512). History is inside `system_prompt` (see `_build_full_prompt`)
  so it is counted via step 2.
- `compose_from_rows`: defensive — `facts` is usually small, but a long roster can be large. `reserved =
  _estimate_tokens(system_prompt) + _estimate_tokens(question_framing)`; `facts =
  _fit_text_to_budget(facts, reserved, self.num_ctx, num_predict=900)` before building the prompt.

## 6. Data flow / behavior

| Case | Behavior |
|---|---|
| Bundle ≤ budget (typical at 16k, incl. cap-gap ~9.3k) | No-op — all chunks whole. Bug fixed because nothing overflows. |
| Bundle > budget | Whole pages by rank until full → verbatim prefix on the boundary page → stop. Dropped pages' links surfaced in ADDITIONAL SOURCES. |
| Rank-1 alone > budget (108KB grid) | Rank-1 verbatim prefix + its Source link. (Matched-section extraction deferred — §7.) |
| Empty chunks | Unchanged: "No relevant context found." |
| Degenerate budget ≤ 0 | rank-1 prefix + warning log. |

## 7. Deferred (loudly flagged — NOT silently dropped)

- **Matched-chunk-aware truncation.** When an over-budget page must be trimmed, truncating to the
  *matched passage* (highest-CE chunk) rather than the page prefix would preserve answers that sit
  mid-page. Requires `ce_score` + matched-chunk, which live on the unmerged gate branch — not on main.
  **Folds in with the chunk work at the DB-rebuild / Phase-3 cutover.** Impact on main is limited: at 16k
  truncation only fires on >~56k-char bundles, and the canonical cap-gap answer is a page-prefix anyway.
- **Grid "matched section + link" extraction.** Same dependency (needs chunk/section machinery). On main,
  a monster grid is handled by verbatim-prefix + link, which is a safe subset of the approved exception.
- **`model_descriptor`-based exact token counting.** Adopt when the descriptor merges (rebuild); the
  char-based estimate is the LLM-agnostic interim and remains a valid fallback.

## 8. Testing (TDD)

Unit tests on the pure functions (no live model needed → deterministic CI):
- `_estimate_tokens` monotonic & conservative (estimate ≥ a known lower bound for ASCII).
- `_fit_chunks_to_budget`: under-budget = identity (same objects, untouched); over-budget = whole pages
  packed in rank order then one prefix-truncated boundary chunk; rank-1-alone-overflow = single prefix
  chunk, never dropped; originals never mutated; prefix cut on whitespace (no mid-word split); dropped
  chunks' URLs returned for link surfacing; degenerate budget → rank-1 prefix.
- `_build_context_block`: ADDITIONAL SOURCES block lists dropped-chunk URLs; included chunks keep their
  Source line.
- **Binding regression (the bug):** assemble the prompt for the cap-gap query against a synthetic 5×long-
  page bundle (~9.3k tok) and assert `_estimate_tokens(system + user) + num_predict ≤ num_ctx`. Deterministic,
  model-free — pins "never silently overflow" forever. The original page text (incl. the cap-gap first
  sentence) is present in the assembled prompt.
- Add the cap-gap question to `eval/questions.txt` (a live-pipeline check for the human eval run).

**Manual verification before merge:** run the real query through `scripts/ask.sh --answer` with Ollama up
at num_ctx=16384 and confirm a cap-gap answer (never "The"), plus a spot-check that `prompt_eval_count` is
no longer pinned at the cap. (Evidence-before-claim: show the output.)

## 9. Hard lines honored

- **LLM-agnostic:** budget derived from `num_ctx`/`num_predict`; char-based estimate works for any model;
  no model-specific tokenizer baked in.
- **use-max-capacity:** 16k window sized to the model's real working capacity on this hardware; budget
  fills the window rather than an arbitrary small cap.
- **verbatim / never-withhold:** included/truncated content is verbatim (prefix slice, whitespace-snapped);
  every source link preserved (included, truncated, or dropped); truncation is explicit + link-backed, the
  generalization of the approved grid exception.
- **crawl = mechanical-only:** this is generation-side, no crawl/content rewriting; prefix slicing is
  mechanical, not editorial.
- **evidence-before-claim:** binding model-free regression test + manual live verification with shown output.

## 10. Goals checklist (for the close-out — shipped vs deferred)

- G1 — Prompt can never silently overflow `num_ctx` (explicit budgeted packing). **SHIP.**
- G2 — Cap-gap query answers correctly, never "The"; pinned by a binding regression test. **SHIP.**
- G3 — `num_ctx` raised to 16384. **SHIP.**
- G4 — Budget derived (LLM-agnostic), char-based conservative token estimate. **SHIP.**
- G5 — Verbatim-prefix truncation + all source links preserved (never-withhold). **SHIP.**
- G6 — Applied to both `generate_answer` and `compose_from_rows`. **SHIP.**
- G7 — Matched-chunk-aware truncation. **DEFERRED** → rebuild/Phase-3 cutover (needs gate-branch chunk work).
- G8 — Grid "matched section + link" extraction. **DEFERRED** → same dependency; interim = prefix + link.
- G9 — `model_descriptor` exact token counting. **DEFERRED** → adopt at rebuild; char-estimate is the interim.

## 11. Reject criteria

- Any assembled prompt whose estimated tokens + num_predict can exceed `num_ctx` → reject.
- The cap-gap regression test answering "The" / failing the token-bound assertion → reject.
- Any source link silently lost when its body is trimmed/dropped → reject (never-withhold).
- Original `RetrievedChunk` objects mutated in place → reject (purity).

## 12. Build sequence (for writing-plans)

1. `num_ctx` 8192→16384 + `_estimate_tokens` + constants (tests).
2. `_fit_chunks_to_budget` pure fn (tests: all §8 cases).
3. `_build_context_block` ADDITIONAL SOURCES link surfacing (tests).
4. Wire into `generate_answer` + `compose_from_rows` (tests + binding regression).
5. `eval/questions.txt` += cap-gap query.
6. Manual live verification (Ollama up, show output).

## 13. Cutover (D6)

After both expert reviews fold + owner sign-off on the diff: **merge `feat/context-budget-guard` → main**
and `bash scripts/restart.sh` (code change → restart required). This piece is prod at end of cycle. The
gate refit + deferred matched-chunk/grid refinements ship to main later via the DB-rebuild's own cutover.

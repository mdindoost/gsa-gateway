# Context-Budget Guard — design (rev 2)

**Date:** 2026-06-28
**Branch:** `feat/context-budget-guard` (off `main` @ `69fcfd6`)
**Status:** rev-2 — both hard-gate reviews folded (senior-eng NO-GO + RAG GO-WITH-CHANGES, 2026-06-28);
awaiting Codex re-review of rev-2 → owner sign-off → TDD build.
**Arc:** Phase-3 Task #1 of the teacher-eval / answer-stack arc ([[project_answer_stack_design]],
[[project_m2_embedding]], [[project_teacher_eval]]). Corpus-independent; ships to prod independently of
the gate refit / DB-rebuild (Decision A = hybrid; Decision B = fresh branch off main).

> **rev-2 changelog (reviews folded):** estimator → tiktoken + safety factor (was chars/3.5, not a true
> upper bound); architecture → assemble-measure-shrink over the FULL rendered prompt (was body-only budget);
> ADDITIONAL SOURCES URL block REMOVED (unbudgeted + unsafe 8B framing); `compose_from_rows` over-budget →
> returns `None` (no truncate-then-rephrase); conversation history bounded before packing; chunk order
> preserved (no re-sort); truncated-chunk copy preserves provenance; whitespace-snap hard-cut fallback;
> degenerate-budget handled; `num_ctx` config/env override; claims weakened to honest wording; quality-cap
> added as a deferred goal.

---

## 1. Problem

Live prod bug, confirmed 2026-06-27: **"what is the H-1B cap gap period for F-1 students"** returns just
**"The"** (Source: Office of Global Initiatives).

Root cause (proven read-only end-to-end; re-verified in current code 2026-06-28):
- `bot/services/ollama_client.py` runs generation with `num_ctx=8192`.
- `_build_context_block` (line 165) packs the **full `.text` of every retrieved chunk** the caller passes
  (top-k from the retriever, k≈5) with **no token budgeting**. Its comment ("each chunk is now a single
  focused item … no bloated document to truncate") is false for long crawled policy/immigration pages.
- The cap-gap top-5 OGI pages total ~37,273 chars ≈ 9,300 tokens; the full prompt is ~10–11k tokens >> 8,192.
- Ollama **silently truncates the FRONT** of the prompt on overflow (documented in the code comment at
  lines 149–154) → mangled context → llama3.1:8b degenerates to "The".
- Proven: top-chunk-only (2,949 tok, fits) → correct full answer; overflow (~12.9k tok) →
  `prompt_eval_count` capped at 8191 → output "The".

It breaks on exactly the richest long policy/immigration pages (OGI, financial aid, …) — the
generation-layer face of the M2 long-page problem.

**Why chunking does not fix it:** Modified-A / Phase-2 deep-fallback returns the **full parent page** to
the LLM (never-withhold) → a 12k-char parent recreates the overflow. A context-budget invariant is required
**regardless of chunking**, and is a **prerequisite** before enabling either the long-page path or Phase-2's
deep-fallback flag.

## 2. Goal & non-goals

**Goal (honest wording, per reviews):** the assembled generation prompt is **explicitly budgeted** to stay
within `num_ctx` — never Ollama's silent front-truncation. The budget uses a **conservative token over-count
(tiktoken + safety factor) plus a fixed cushion below `num_ctx`**, which prevents overflow for realistic NJIT
content (English prose + URLs/IDs). Exact, model-exact tokenizer counting is deferred (G9). The canonical
cap-gap query (and the long-page class it represents) answers correctly, never "The".

**Non-goals (explicitly out of scope):**
- No retrieval changes (ranking, fusion, CE, chunk logic) — pure generation-layer plumbing.
- No gate refit / abstain logic (later Phase-3 cutover, folded into the rebuild).
- No chunking / `model_descriptor` / matched-chunk machinery (unmerged gate branch).
- No quality-oriented context cap (separate follow-up — G10). The guard does **not** pass more pages than
  today (still top-k); it only stops front-truncating them into garbage, so it is strictly better than the
  broken state.
- No merge of any unmerged branch; ships standalone off main.

## 3. Decisions (locked with owner; rev-2 folds reviews)

- **D1 — `num_ctx` 8192 → 16384**, configurable via constructor + env/config override (SE#14). Hardware
  verified: RTX 4070 Ti SUPER 16 GB; llama3.1:8b @ 16k ≈ 8.5–9 GB → ~7 GB free. Document the expected
  quantization; if Ollama OOMs, the override lets ops drop it.
- **D2 — Budget is DERIVED from the model, never hardcoded**, and measured over the **full rendered prompt**
  (system + user), not just bodies. `target = num_ctx − num_predict − CUSHION`; pack/shrink until the
  estimated whole prompt ≤ target.
- **D3 — Token estimate = tiktoken (`cl100k_base`) × `SAFETY_FACTOR` (1.2)**, with a char-based fallback
  (`ceil(chars / 3.0)`) if tiktoken import fails. tiktoken gives real subword counts (URLs/code/IDs handled
  far better than a flat char ratio); the safety factor covers llama-vs-tiktoken divergence; the `CUSHION`
  (D2) absorbs residual error. This is a conservative counting heuristic, **provider-isolated** (not the
  generation model's tokenizer — no LLM coupling). Honest limit: not provably model-exact → G9 defers exact
  counting; the claim is "budgeted + conservative + cushioned," not "mathematically impossible to overflow."
- **D4 — Truncation = verbatim prefix + in-body marker.** An over-budget boundary page is cut to a
  whitespace-snapped verbatim prefix (hard-cut fallback if no whitespace fits) with a model-facing marker
  appended: *"[Document truncated to fit the context budget; later sections are not shown — open the Source
  link for the full page.]"* (tells the 8B the content is partial → no false completeness; reinforces the
  link). Matched-chunk (answer-span) truncation DEFERRED (§7, needs gate-branch chunk work).
- **D5 — Never-withhold via the reply's source link, NOT via prompt-stuffed URLs.** The existing reply
  source-note (`_source_note_for`, message_handler) already surfaces the answer's source link to the user.
  Dropped lower-ranked pages of an oversized bundle are **not** added to the model prompt (the rev-1
  ADDITIONAL SOURCES block is REMOVED — it was unbudgeted and let the 8B hallucinate citations from URL
  slugs). Honest framing: source links preserve **user access**; truncated/dropped bodies are **not evidence
  for generation**.
- **D6 — Cutover = merge to main + restart on sign-off.** Live bug fix, corpus-independent → lands on prod
  at the end of its own hard-gate cycle. The gate refit + deferred matched-chunk/grid refinements ship to
  main later via the DB-rebuild's own cutover.

## 4. Architecture — assemble, measure, shrink

One budgeting path inside `bot/services/ollama_client.py`. Instead of budgeting bodies and hoping the
framing fits, we **build the real prompt, measure it, and shrink until it fits**, with a final hard guard.

```
generate_answer
  1. bound history (last MAX_HISTORY_TURNS turns)         ← deterministic, newest-first
  2. system_prompt = BASE + bounded-history
  3. fitted = _fit_chunks(chunks, system_prompt, question, num_predict)   ← NEW: measure-shrink loop
        repeat:
          context_block = _build_context_block(included)   ← real rendered text, incl. all framing
          user = context_block + question + INSTRUCTIONS
          est = estimate(system_prompt) + estimate(user)
          if est + num_predict <= num_ctx - CUSHION: break
          else: shrink(included)   ← drop lowest-ranked whole page; if only 1 left, prefix-truncate it
  4. payload(system_prompt, user, num_ctx)                 ← final est asserted <= num_ctx (log+shrink if not)
```

`shrink` order: drop the **lowest-ranked included page** first (preserve input/rank order — no re-sort,
SE#6); once a single page remains and still doesn't fit, **prefix-truncate** it (D4); rank-1 is never
dropped, only truncated. The loop terminates (each pass removes content; floor = rank-1 hard-cut).

Rejected: budgeting bodies only with a `SAFETY_MARGIN` (rev-1) — undercounts framing/URLs → could overflow
(SE#2/#3). Rejected: guard at retriever/caller layer — duplicated logic, drift. Rejected: num_ctx-raise
alone — doesn't scale to monsters.

## 5. Components

### 5.1 `OllamaClient.__init__`
`num_ctx` default 8192 → **16384**, read from config/env (`OLLAMA_NUM_CTX`) with that default. Both call
paths already read `self.num_ctx`.

### 5.2 `_estimate_tokens(text: str) -> int` (NEW, module-level)
tiktoken `cl100k_base` (lazy-loaded, cached module-level): `ceil(len(enc.encode(text)) * SAFETY_FACTOR)`,
`SAFETY_FACTOR = 1.2`. Fallback on import/encode failure: `ceil(len(text) / 3.0)`. Pure; deterministic.

### 5.3 `_fit_chunks(chunks, system_prompt, question, num_predict) -> list[RetrievedChunk]` (NEW)
- `target = num_ctx − num_predict − CUSHION` (`CUSHION = 1024`, module constant).
- Start with all chunks in **input order** (callers provide rank order — no sort, SE#6).
- Loop: render the candidate context block + user prompt with the current included set, estimate
  `system + user`, and if `est + num_predict > num_ctx − CUSHION`, remove the **last** (lowest-ranked)
  whole page and repeat.
- When only one page remains and it still overflows: replace it with a **prefix-truncated copy** sized so
  the rendered prompt fits, whitespace-snapped, hard-cut fallback if no whitespace within budget, with the
  D4 marker appended (the marker's tokens are inside the measured prompt → counted).
- Truncated/altered chunks are produced via `copy.copy(chunk)` then `.text = …` (or `dataclasses.replace`
  for dataclasses) so `item_id`/`source_url`/`verified` and any runtime attrs survive (SE#7).
- Originals never mutated. Returns the fitted list (≥1 chunk when input non-empty).
- **Degenerate case** (SE#4/#5): if even rank-1 hard-cut to a minimum floor (`MIN_DOC_TOKENS = 128`) can't
  fit because `system + framing + num_predict` alone ≥ `num_ctx − CUSHION` (pathological history despite the
  D-bounded turns), log a warning and return `[]`; the caller treats empty fitted-context as a generation
  miss (returns `None` → existing deflection/fallback), never a silent overflow.

### 5.4 `_build_context_block` change
No ADDITIONAL SOURCES block (removed). Included pages keep their `Source:` line as today. A prefix-truncated
page additionally shows the D4 truncation marker at the end of its body. Nothing else changes in the block.

### 5.5 `generate_answer` / `_build_full_prompt` wiring
`_build_full_prompt` is refactored to: bound history → build `system_prompt` → run `_fit_chunks` → build the
final user prompt from the fitted set. History bounding: keep the **last `MAX_HISTORY_TURNS = 6`** turns
(each already clipped to 400 chars), newest-first, before appending to the system prompt (SE#5). num_predict
stays 512.

### 5.6 `compose_from_rows` (SE#9 / RAG#7)
`facts` is the COMPLETE structured answer; its contract is "include every item." So we do **not** truncate
facts. If `estimate(system + facts + framing) + num_predict > num_ctx − CUSHION`, return **`None`** (log)
→ the caller already falls back to the deterministic facts text (the documented behavior). No truncate-then-
rephrase. (In practice facts rarely approach 16k; this is the defensive floor.)

## 6. Behavior

| Case | Behavior |
|---|---|
| Bundle fits target (typical at 16k, incl. cap-gap ~9.3k) | No-op — all chunks whole. Bug fixed (nothing overflows). |
| Bundle > target | Drop lowest-ranked whole pages (rank order) until the rendered prompt fits. Dropped pages: not in prompt; answer's own source link still shown in the reply. |
| Single page > target (108KB grid) | Prefix-truncate it (verbatim) + D4 marker + its Source line. |
| Empty chunks | Unchanged: "No relevant context found." |
| Degenerate (system+framing alone ≥ ceiling) | Return `[]`/`None` → existing deflection; warning logged. Never overflow. |
| Over-budget structured facts | `compose_from_rows` → `None` → deterministic facts text. |

## 7. Deferred (loudly flagged — NOT silently dropped)

- **G7 Matched-chunk-aware truncation.** Truncate an over-budget page to its *matched passage* (highest-CE
  chunk) rather than the page prefix → preserves mid/late-page answers (RAG#1: prefix can drop the matched
  span for immigration FAQs / financial-aid grids / alphabetized lists). Needs `ce_score`+matched-chunk
  (unmerged gate branch). **Folds in at the DB-rebuild / Phase-3 cutover.** Until then, prefix + marker +
  link is the honest interim; impact bounded because at 16k truncation fires only on >~target-size bundles.
- **G8 Grid "matched section + link" extraction.** Same dependency; interim = verbatim prefix + link (a safe
  subset of the approved exception).
- **G9 Model-exact token counting** (`model_descriptor` / llama tokenizer). tiktoken+factor+cushion is the
  conservative interim; adopt exact counting at the rebuild.
- **G10 Quality-oriented context cap** (RAG#5). More full long pages *may* distract the 8B even when they
  fit; a quality cap (reserve room for N distinct sources / prefer shorter high-rank pages / cap body tokens
  below num_ctx) is a separate tuning. Add eval cases comparing full-16k vs capped context; do NOT block the
  bug fix. Logged with telemetry (§8) so the decision is data-driven later.

## 8. Testing (TDD)

Model-free unit tests (deterministic CI):
- **`_estimate_tokens` over-counts vs a raw tiktoken count** on adversarial samples (long URLs, code,
  alphanumeric IDs, CJK, emoji, whitespace-heavy) — assert `_estimate_tokens(x) ≥ raw_tiktoken(x)` (the
  safety factor) so the estimate is a conservative-vs-its-own-tokenizer bound. (Honest caveat: this bounds
  vs tiktoken, not vs llama — G9. The CUSHION is the backstop for that gap.)
- **`_fit_chunks`**: fits-as-is = identity (same objects); over-budget = lowest-ranked pages dropped in
  rank order; single-page-overflow = one prefix-truncated copy with marker, rank-1 never dropped; originals
  unmutated; truncated copy preserves `item_id`/`source_url`/`verified`; whitespace-snap + no-whitespace
  hard-cut; degenerate → `[]`.
- **Full-prompt-within-budget**: build the real `system + user` for a synthetic oversized bundle with long
  URLs, long section titles, unverified tags, missing `source_url`, duplicate URLs → assert
  `estimate(system)+estimate(user)+num_predict ≤ num_ctx` after fitting. (This is the invariant SE#2 demanded:
  measure the rendered prompt, not the bodies.)
- **No ADDITIONAL SOURCES block** present in any rendered prompt (regression against rev-1).
- **`compose_from_rows`**: over-budget facts → returns `None` (no truncated facts sent); under-budget → normal.
- **Mocked overflow guard** (SE#10): mock the assembled-prompt estimate at/over `num_ctx` and assert the fit
  loop shrinks below it (a test that fails if the loop ever yields an over-budget prompt).
- **Binding regression (the bug)**: assemble the prompt for the cap-gap query against a synthetic 5×long-page
  bundle (~9.3k tok) and assert the rendered prompt + num_predict ≤ num_ctx AND the cap-gap first sentence is
  present in the prompt. Honest scope (SE#12/RAG#8): this pins **no-overflow + evidence-present**, NOT answer
  correctness — correctness is covered by the manual live check below.
- **Telemetry test**: the fit step logs included/truncated/dropped doc-ids + estimated tokens (feeds G10).
- Add the cap-gap question to `eval/questions.txt`.

**Manual verification before merge (required evidence):** run the real query via `scripts/ask.sh --answer`
with Ollama up at num_ctx=16384 → confirm a cap-gap answer (never "The") and that `prompt_eval_count` is no
longer pinned at the cap. Show the output (evidence-before-claim).

## 9. Hard lines honored

- **LLM-agnostic:** budget derived from `num_ctx`/`num_predict`; tiktoken is a provider-isolated counting
  heuristic (not the gen model's tokenizer); char fallback works for any model; no model-specific constant baked in.
- **use-max-capacity:** 16k sized to real working capacity on this hardware; tiktoken+cushion fills far more
  of the window than the old 8k while staying safe. (Stated honestly: the cushion leaves deliberate headroom
  for estimator error — not "maximal to the last token," but maximal-safe.)
- **verbatim / never-withhold:** included/truncated content is verbatim (whitespace-snapped slice); the
  answer's source link is preserved in the reply; truncation is explicit + link-backed + marked. Honest:
  links = user access, dropped bodies are not model evidence.
- **crawl = mechanical-only:** generation-side; prefix slicing is mechanical, not editorial.
- **evidence-before-claim:** model-free invariant tests + required manual live verification with shown output.

## 10. Goals checklist (close-out — shipped vs deferred)

- G1 — Assembled prompt explicitly budgeted to stay within `num_ctx` (measure-shrink over the full rendered
  prompt; conservative tiktoken over-count + cushion; no silent truncation). **SHIP.**
- G2 — Cap-gap query: model-free regression pins no-overflow + cap-gap evidence present; **manual live
  verification** confirms the answer (never "The") before merge. **SHIP (with honest split).**
- G3 — `num_ctx` 16384, config/env override. **SHIP.**
- G4 — Budget derived; tiktoken+factor estimator with char fallback. **SHIP.**
- G5 — Verbatim-prefix truncation + marker; source link preserved for **user access** in the reply.
  Matched answer-span preservation **DEFERRED** under G7. **SHIP (scoped).**
- G6 — Guard on `generate_answer`; `compose_from_rows` over-budget **falls back deterministically** (returns
  `None`) rather than rephrasing incomplete facts. **SHIP.**
- G7 — Matched-chunk (answer-span) truncation. **DEFERRED** → rebuild/Phase-3 (needs chunk work).
- G8 — Grid "matched section + link". **DEFERRED** → same dependency; interim = prefix + link.
- G9 — Model-exact token counting. **DEFERRED** → rebuild; tiktoken+cushion is the interim.
- G10 — Quality-oriented context cap. **DEFERRED** → data-driven follow-up; telemetry added now.

## 11. Reject criteria

- Any rendered prompt whose estimated tokens + num_predict can exceed `num_ctx` after fitting → reject.
- The cap-gap regression answering "The" / failing the token-bound assertion (or the manual live check) → reject.
- ADDITIONAL SOURCES / any bare dropped-URL block reappearing in the model prompt → reject (RAG#3).
- `compose_from_rows` sending truncated "complete" facts to the model → reject (SE#9/RAG#7).
- Original `RetrievedChunk` objects mutated in place, or truncated copies losing provenance → reject.
- Chunks re-sorted (rank order not preserved) without a proven-equivalent test → reject (SE#6).

## 12. Build sequence (for writing-plans)

1. `_estimate_tokens` (tiktoken + factor + char fallback) + constants (`SAFETY_FACTOR`, `CUSHION`,
   `MAX_HISTORY_TURNS`, `MIN_DOC_TOKENS`) + `num_ctx` 16384/env (tests).
2. `_fit_chunks` measure-shrink loop: drop-lowest-rank → single-page prefix-truncate + marker → degenerate
   `[]`; provenance-preserving copy; no re-sort (tests: all §8 cases).
3. `_build_context_block`: remove ADDITIONAL SOURCES; add truncation marker rendering (tests).
4. `_build_full_prompt`: history bounding + reorder + wire `_fit_chunks`; `generate_answer` returns `None`
   on empty fitted context (tests + binding regression + full-prompt-within-budget + mocked-overflow).
5. `compose_from_rows`: over-budget → `None` (tests).
6. Telemetry logging of included/truncated/dropped ids + tokens.
7. `eval/questions.txt` += cap-gap query.
8. Manual live verification (Ollama up, show output).

## 13. Cutover (D6)

After the Codex re-review of rev-2 + owner sign-off on the diff: **merge `feat/context-budget-guard` → main**
and `bash scripts/restart.sh` (code change → restart). Prod at end of cycle. The gate refit + deferred
matched-chunk/grid refinements ship to main later via the DB-rebuild's own cutover.

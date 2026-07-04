# ask.sh + eval.sh — correctness & faithfulness fixes (design of record)

**Date:** 2026-07-04
**Status:** Fable-reviewed (findings + fix sketches) → owner approved scope (BOTH tools, all dev-tool
fixes; defer the optional bot-side `is_abstain` field) → build TDD wave-by-wave, Fable diff per wave.
**Source of findings:** Fable full audit of `scripts/ask.sh`→`trace_query.py` and `scripts/eval.sh`→
`eval_run.py`/`eval_judge.py`/`eval_report.py`, verified against the live pipeline
(`ROUTER_V21=1 SHADOW=0`, `ANSWER_GATE_ENABLED=1`, `LIVE_ENABLED=1`, `FOLLOWUP_RESUME_ENABLED=1`,
`RETRIEVAL_DEEP_FALLBACK=1`, gen=`granite4:tiny-h`, Qwen-1024 embeddings).

## Why
Both tools are used constantly to debug + evaluate the bot, and both have DRIFTED from the current
pipeline. The X-ray can misreport routing (shows the legacy deterministic router, not the live
UnifiedRouter) and crashes on `--answer -v`; the eval miscounts live answers as KB and abstentions as
answered, mis-parses the judge, and — worst — **deletes real students' analytics rows**.

## Findings + fixes (ranked; grouped into build waves)

### Wave 1 — `ask.sh` crash + stale docs (trivial, no output-semantics change)
- **C2** `trace_query.py:110-115` — `--answer -v` calls `OllamaClient._build_full_prompt` (removed in the
  prefit/num_ctx refactor) → `AttributeError`. Fix: build the prompt via the current API —
  `oc._build_system_prompt(None)`, `fitted = oc.prefit(q, v1)`,
  `oc._assemble_user(oc._build_context_block(fitted), q)`; header shows `kept N/M chunks`.
- **M4** `ask.sh:2-4`, `trace_query.py:2-13,114` — stale headers ("heads-up" removed 2026-06-25;
  "router = deterministic structured route"). Rewrite to reflect UnifiedRouter + tier verdict + WS4 gate.

### Wave 2 — eval classification + judge correctness (**output numbers WILL re-base**)
- **C3** `eval_run.py:28,49` — `_LIVE="From NJIT's website:"` never matches the real prefix
  `"🌐 Live from NJIT's website (fetched live): "` → live column permanently 0. Fix: classify off the
  existing `MessageResponse.is_live` flag (set on all live paths), not answer text.
- **C4** `eval_run.py:27,46` — abstentions escape the deflect check (`_useful_abstain` says "a specific
  **answer**", plus `LIVE_NOT_FOUND_MSG` and the `_rag_pipeline` error text) → counted `kb`, inflating
  answered% and handing hedges to the judge. Fix: match the shared `"For accurate information, please:"`
  block + imported `LIVE_NOT_FOUND_MSG` + the error marker.
- **H1** `eval_judge.py:35` — `"CORRECT" in u` matches "INCORRECT"/"PARTIALLY CORRECT" → wrong grades.
  Fix: ordered `\bWRONG|INCORRECT\b` → `\bPARTIAL(LY)?\b` → `\bCORRECT\b` word-boundary checks.
- **H4** `eval_judge.py:33` — answer truncated `[:1200]` cuts rosters/live extracts mid-list (gen runs
  ~512 tokens ≈ >2000 chars). Fix: `[:4000]`.
- **M5** `eval_run.py:83-85` — capture `is_live` + `offer_live_search` on each rec so a deflect is
  actionable (offer-on-deflect ⇒ live tier not tried).
- **M7** `eval_report.py:66` — `error` rows never enter the gap list; add them.
- **⚠ Numbers shift:** live% goes >0, answered% drops (abstentions stop counting). Historical runs not
  comparable — print a one-line note in the report header.

### Wave 3 — analytics-safety + robustness (protects real user data)
- **C5** `eval.sh:14,23` + `trace_query.py:133,136` — watermark delete (`id > WM`) removes REAL users'
  `questions` rows logged during the run. Fix: delete by synthetic `user_id_hash` (`hash_user_id(f"eval-{i}")`
  / `hash_user_id("trace")`), in `eval_run.py`'s `finally` (self-heals crashed runs). eval.sh drops the
  WM plumbing entirely.
- **H2** `eval.sh:11` — `PY=.venv/bin/python` no fallback. Fix: `[ -x "$PY" ] || PY=python3` (match ask.sh).
- **H3** — `set -e` + cleanup-after-judge: a mid-run crash skips cleanup. Fixed structurally by C5's
  `finally`.
- **L1** `eval_run.py` — `out`/`asst`/`db` not closed on exception → wrap in `try/finally` (folds into C5).

### Wave 4 — `trace_query` faithfulness rebuild (biggest diff)
- **C1** `trace_query.py:33,78-86` — Stage-1 ROUTER shows legacy `route()`, not the live
  `UnifiedRouter.decide()`. Fix: build the router via `maybe_build_unified_router(db_path=DB, embedder=emb,
  intent_detector=IntentDetector(), generate_json=partial(generate_json_sync, base_url, model))` (mirrors
  `assistant.py:138-141`), print `RouteDecision(family, skill, args, source, score)` + the deterministic
  `route()` alongside for contrast; note EMPTY structured → production degrades to RAG.
- **M1** — add a "TIER VERDICT" stage (no network): `top_ce < LIVE_THRESHOLD` → office→deep-fallback→live,
  using the live flags/thresholds, so the trace shows what production does with the pool.
- **M2** — surface the WS4 gate: fast mode prints Gate-1 intent verdict; `--answer` enables scoped DEBUG
  logging so the gate/deep-fallback reasons appear.
- **M3** `trace_query.py:98` — print the reranker's STORED `ce_score` (what `top_relevance` uses), not a
  second recomputed CE pass.
- **M6** `trace_query.py:39` — use `config.database_path` (honors `DATABASE_PATH`), not a hardcoded path.

### Wave 5 (conditional) — bot-side `is_abstain` field — **owner: ask Fable at the end; if Fable accepts, BUILD it**
- Add `is_abstain: bool` (+ maybe `abstain_reason`/`route_tag`) to `MessageResponse`, set at the four
  abstain sites (Gate-1 `:353`, gate-abstain `:1043-1046`, no-chunks `:1065`, explicit-live-miss `:749`).
  Additive, zero answer-behavior change, but touches live bot → full senior-eng gate. Ends the eval
  answer-text coupling permanently (Wave 2's marker-matching becomes `getattr(r,"is_abstain",False)`).
- **Owner directive 2026-07-04:** after Waves 1–4 ship, dispatch Fable on THIS change specifically; if
  Fable accepts → build under TDD + Fable diff-review → commit + restart, then rewire `eval_run.classify`
  to key off `is_abstain`. If Fable rejects → stays deferred, Wave 2's marker-matching stands.

## Testing
- Pure functions get unit tests: `eval_run.classify(ans, r)` (live/abstain/kb across all templates),
  `eval_judge` parse mapping (INCORRECT→wrong, PARTIALLY CORRECT→partial, CORRECT→correct),
  `eval_report` error-in-gaps. Add to `bot/tests/` or `v2/tests/`.
- `trace_query`/`eval.sh` end-to-end: smoke-run (Ollama is up) — `--answer -v` must not crash; a live
  answer must classify `live`; an abstain must classify `deflect`.

## Constraints honored
Dev tools only (no live answer-behavior change); eval stays self-contained + repeatable; classify/branch
off structured signals (`is_live`, imported constants) over text prefixes; surgical, faithful-to-production
— not a rewrite. No DB writes beyond the (now hash-scoped) analytics cleanup.

## Goals checklist (shipped/deferred — filled per wave)
- W1 C2+M4 · W2 C3+C4+H1+H4+M5+M7 · W3 C5+H2+H3+L1 · W4 C1+M1+M2+M3+M6 — each Fable diff-reviewed.
- Deferred + loudly flagged: bot-side `is_abstain` field; `--sample` (L2); self-grade judge honesty
  labels/`--judge-model` (H5) — nice-to-have, note if skipped.

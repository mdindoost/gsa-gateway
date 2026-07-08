# Senior-Eng Review — Processing-Debt Pilot Implementation Plan

*Date: 2026-07-06 · Reviewer: senior-eng (general-purpose agent, verified against real codebase) · Verdict: **BUILD AFTER MUST-FIXES***

> Preserves the review verbatim-in-substance so the fixes aren't lost. Fold targets = the plan
> `2026-07-06-implementation-plan-processing-debt-pilot.md`. Direction of every crux bug = it
> UNDER-reports debt (the cardinal sin) → all must-fix before the crux is trustworthy.

## MUST-FIX
1. **kg_probe span is the bare node NAME → KG attribute facts wrongly NOT_OWNED. [T6]** Span must be
   built from node `name` + `attrs` JSON + incident `edges` titles/categories (real structured content),
   not `r["name"]`. Current `test_kg_probe_finds_person` never runs entailment so it hides this.
2. **embed_probe silently returns [] in prod — two causes. [T6]**
   (a) Build the Embedder from the ACTIVE DESCRIPTOR (Build B = Qwen 1024-d; `knowledge_vectors.embedding
   FLOAT[dim]` from `active_descriptor().dim`). A bare `Embedder()` whose model ≠ active descriptor →
   width mismatch → OperationalError → []. Assert `len(vec)==active_descriptor().dim`.
   (b) Use the PROVEN KNN SQL from retriever.py:376-380 `_semantic`: `WHERE embedding MATCH ? ORDER BY
   distance LIMIT ?` with `sqlite_vec.serialize_float32(vec)`. NO `k = ?` (that's only for
   `knowledge_chunk_vectors`). Add a live non-zero assertion in T14 (paraphrase must return hits).
3. **grep_probe scope too narrow + span is a fact-fragment. [T6]** Grep over `content` + `title` +
   `nodes.attrs` JSON (design §3.2), not content only. Set evidence span to the DB window around the
   match, not `ph` (a slice of the claim → trivially self-confirms). Also: fts_probe span truncated to
   `content[:300]` → supporting sentence past char 300 fails entailment; use match-centered window / full content.
4. **`_EXCLUDED_TYPES={"publication"}` is WRONG → CONFIG misses `syllabus`. [T9]** Real default =
   `frozenset({"publication","syllabus"})` (retriever.py:148) AND admin-tunable via `settings`
   (`retriever._load_exclude`). READ the live `retriever.exclude_types` from the same conn. (CLAUDE.md's
   "excludes ONLY publication" is stale — trust the code.)
5. **SC6 (oracle-correctness rate) silently dropped. [T9/T13, G3]** Compute
   `(DROPPED_ORACLE + we_are_authority) / guarded-total`, render it, add the >30% SC6 gate. Verdicts
   already exist from the guard — just never aggregated.
6. **Confirm Brave ANSWERS entitlement is a HARD GATE before T15. [T2/T14]** Key exists, but verify
   `POST /res/v1/chat/completions` actually returns (not 401/403). Pull the single cached live probe
   forward; block T15 on it. Top external risk. (NOTE: we already verified this live earlier in the
   session — brave-pro returned a real answer — but keep the gate.)

## SHOULD-FIX / nits
- **`Route` has no `.family`** (router.py:323 `Route(skill,args)`). Drop `xray.router_family` or derive
  from skill. Also Global-Constraints doc claims `generate_json_sync(...,temperature=0.0)` — no such
  kwarg (temp hardcoded); fix the doc.
- **Read-only is by-discipline only.** `get_ro_connection`→`get_connection` opens RW. Use
  `sqlite3.connect(f"file:{db}?mode=ro", uri=True)` + load sqlite-vec. ALSO acknowledge `ask.sh --answer`
  runs the live `message_handler` which INSERT+DELETEs an analytics `questions` row per Q (ops-DB write;
  matches eval.sh's accepted pattern — but the "no writes" claim must say so).
- **`ask.sh --answer` runs the FULL live pipeline incl. the njit.edu Brave live-fallback** on KB-miss →
  (i) uncounted Brave *Search* spend outside the ≤50 cap; (ii) a fact answered only from live web counts
  IN_ANSWER, confounding the "surfaced from OUR KB/KG" thesis + controls. DECIDE: capture with
  `LIVE_ENABLED=0`, or record `resp.source_note` and segregate live-answered facts. (Recommend LIVE_ENABLED=0.)
- **X-ray fidelity gap. [T7]** xray builds its own `V2Retriever(limit=50, group_by_entity=False)`, no
  deep-fallback/office tier — different retrieval than the answer actually saw → false POOL/RANK/COMPOSE.
  And `limit=50` truncates the true pool (two legs of pool_size≥40 can exceed 50) → false POOL. Align to
  production retrieval config; take pool at `limit ≥ 2×pool_size`.
- **ROUTER branch too coarse. [T9]** Fires whenever ANY node evidence exists AND `router_skill is None`
  (i.e. every RAG query). Check the OWNING skill (design §3.3) or prefer the servable-chunk branch when a
  servable ki is present.
- **Sampler drops design §5 dedupe/cluster** — logs are head-heavy; cluster before sampling.
- **Answer-delimiter loose** — split on `"FINAL LLM ANSWER"` carries the header suffix + rule line; strip
  to the line after the header rule.
- **≤50 cap by-construction only** — add a hard max-live-calls counter in `ask_oracle`.
- **PII:** questions come from real logs → may carry a student name/email going outbound to Brave. One-time
  PII scan of the sample before sending.

## Guardrail-compatibility (Fable's A & B)
- **Guardrail A (human validates NUGGET SET) CONFLICTS with the positional κ pipeline.** `emit_csv` writes
  in record order; T15 pairs machine vs human by `[:len]` slicing; `cohen_kappa` asserts equal length. A
  rejected/added nugget de-aligns → κ throws or mis-pairs. FOLD A via a STABLE id per fact
  (hash(question+fact_text)); join machine/human by key, not position. → `adjudicate.py` redesign.
- **Guardrail B (three-way yes/no/UNSURE) — no hard conflict but touches 4 bool sites** (`entailment`,
  `presence_check.presence`, `oracle_guard.guard`, `erag_attrib.chunk_yields_fact`) + classify IN_ANSWER.
  Must update all together with explicit UNSURE routing. CRUCIAL: UNSURE must lean OPPOSITE ways —
  IN_ANSWER: unsure→not-in-answer; PRESENCE: unsure→lean PRESENT (else understates debt).

## Goals coverage
G1 ✅(caveats) · G2 ⚠️PARTIAL (probes incomplete, must-fix 1-3) · G3 ⚠️PARTIAL (SC6 missing) · G4 ✅ ·
G5 ✅(live-fallback side-spend uncounted) · G6 ✅. SC1/4/5 ✅. SC2/SC3 are only `print()` → harden to a
real halt before spending. SC6 MISSING.

## Verdict: **BUILD AFTER MUST-FIXES.** Architecture/TDD/injection solid; fix the 4 crux/attribution/report
bugs + harden read-only + Brave-tier gate, then it's a sound ~$3 instrument.

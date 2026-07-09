# Query Correction — acronym dictionary + deterministic router extension (rev 5 — SCOPE PIVOT, C+A)

**Date:** 2026-06-29 · **rev-4 refresh 2026-07-08** · **rev-5 scope pivot 2026-07-08**
**Branch:** `feat/query-correction-salvage` (off `main`)
**Status:** DRAFT rev 5 — **owner APPROVED scope C+A 2026-07-08.** The rev-3/4 LLM-rewrite body below is
kept as the investigation record; rev-5 PIVOTS the build to **hybrid C+A: an always-on acronym dictionary
+ a deterministic router extension (org-type-aware leader rule + role synonyms + closed-lexicon typo
tolerance). The on-miss `llm_rewrite` + name-guard + structure-guard + shadow apparatus is DROPPED from v1
and loudly deferred (G6).** Driven by two $0 probes + Fable's binding confirmation review
(`docs/research/oracle-processing-debt/2026-07-08-fable-review-query-correction-rev4.md` + its follow-up
ruling). The CURRENT build spec is **§14 below**; §§4–13 describe the deferred LLM path. Next: implementation
plan → owner sign-off → TDD. No code yet.
**Arc:** Retrieval/routing robustness. Extends the deterministic router (the 2026-07-03 pattern). Ships
independently; no LLM on the hot path.

> **rev-5 changelog (2026-07-08 — the SCOPE PIVOT; supersedes the rev-3/4 build plan where noted).**
> Two $0 probes + Fable's confirmation review re-scoped this lever. See §14 for the build spec.
> 1. **PROSE-RAG arm is NOT the ⅔** (prose probe, §3b must-fix #4): 17/24 prose-debt queries already clear
>    `LIVE_THRESHOLD` with the typo → their debt is DOWNSTREAM (gate/compose = gate-2's domain), never a
>    retrieval miss the rescue arm sees. Only ~21% are typo-retrieval-misses. The RAG-rescue arm's exclusive
>    value shrinks to a handful of borderline conversions.
> 2. **STRUCTURED arm is the real win, and it's DETERMINISTIC** (structured probe
>    `2026-07-08-structured-route-probe-result.txt`: 16/20 = 80% route to a KG skill after correction). The
>    wins decompose into (a) **abbreviation expansion** → the §5.1 dictionary (safe, no LLM), and (b)
>    **`run`/`boss`/`president`→`chair`/`dean`** contextual mapping.
> 3. **Fable Finding A — the LLM path is SELF-DEFEATING on (b).** §5.3b structure-guard reverts any rewrite
>    dropping a content token without an edit-≤2 survivor. Measured: `run→chair`(5), `boss→chair`(5),
>    `president→chair`(8), `president→dean`(7) are ALL guard-reverted (only `heir→chair`=2 survives — the
>    flagship is the exception). So the two-arm LLM build delivers ≈ZERO of the 10 role wins; the guards that
>    make the LLM safe block the rewrites that make it useful.
> 4. **Fable Finding C — the router already owns the machinery** (`_ROLE_SYNONYM` router.py:130,
>    `ORG_TYPE_LEVEL` + hierarchy-climb :861-871): an org-type-aware **leader rule** (`who runs/heads/boss
>    of/president of <unit>` → chair for dept, dean for college, president for gsa/club — resolved from the
>    org node's ACTUAL type) is a small native extension, and the router KNOWS the org type (granite would
>    guess). The typo residue is edit-≤2 against CLOSED lexicons (role/org/metric vocab) → deterministically
>    fuzzy-matchable INTO known vocabulary (NOT the rejected open-vocabulary G4 fuzzy).
> 5. **DECISION: build C+A (§14); DROP+DEFER the LLM apparatus (G6).** GSA-equal held (mapping is
>    org-type-driven, no GSA bias). Corpus-debt prose residue → the fresh prose-recall track (NOT old-M2);
>    the 4 structured non-wins = a missing skill (metric-by-research-area) + org data gaps, logged not built.

> **rev-4 changelog (2026-07-08 refresh — the ONLY delta for this review; rev-3 body below is unchanged
> and already twice-reviewed).**
> 1. **MODEL RETARGET (llama3.1:8b → granite).** The VRAM diet (2026-07-07) removed llama3.1; only
>    `granite` (gen) + `qwen` (embed) remain. The `llm_rewrite` call (§5.2) now targets the resident gen
>    model via config, NOT a hardcoded model — consistent with the **LLM-agnostic hard line**. This is
>    architecturally SAFE precisely because the design's fidelity enforcement is the two DETERMINISTIC
>    guards (§5.3 name-guard, §5.3b structure-guard), not the prompt — they revert ANY model's bad rewrite.
>    CAVEAT: the §3 P1–P4 change-rate/quality numbers are **llama3.1-specific**; they are NOT re-measured
>    here. The build's shadow FP-measure mode (§5.7/§8, already specified) MUST re-measure granite's
>    change-rate + destructive-rate before serving — this replaces re-running P4 by hand. Granite may
>    rewrite more/less aggressively; the guards + shadow gate contain that.
> 2. **EMPIRICAL "worth building" evidence folded — see new §3b.** The 1000-question DB eval (2026-07-08,
>    LIVE ON) is the proof this design was waiting for: ~183 fixable-debt questions, and their failure
>    mechanism is EXACTLY this spec's target (router-`None` on typos/slang → RAG miss → live). This
>    justifies moving from "awaiting sign-off" to "build."
> 3. **BUTTONS CONTRADICTION FIXED.** §8 line "KG-rescue: … **no `question_id`/buttons**" contradicts the
>    rev-3 reversal (§5.6/§9/§11: rescue answers GET 👍/👎/🔄). rev-4 binds: **buttons ON**; the §8
>    KG-rescue test asserts a logged `question_id` + buttons present. (Corrected in §8 below.)
> 4. **INTEGRATION-DRIFT flags for build time (NOT design changes — re-verify, don't assume).** Much
>    shipped since 2026-06-29: WS3 slot-extraction, **WS4 answer-gate (`ANSWER_GATE_ENABLED`, c44784f)**,
>    followup-resume, area-expansion, person-facets. Two concrete re-checks the plan MUST do: (a) the §5.5
>    four gate-sites are cited by 2026-06-29 line numbers (`:776/:777/:781/:799`, plus `:208/:222/:236/
>    :262/:639/:757/:826-829`) — these have DRIFTED; the build re-locates them by ANCHOR (the `top_relevance`/
>    `is_fact_shaped`/`gate2_prompt`/`live_search` calls), never by the stale number. (b) The WS4
>    answer-gate now sits in this path; confirm `retrieval_q=q2` threads through the WS4 gate too, and that
>    the on-miss block sits correctly relative to WS4 abstention (a WS4 abstain must still allow the q2
>    rescue before live). No design change expected — a wiring re-verification the plan owns.
> 5. ~~**Scope confirmation from the evidence: the design covers BOTH debt buckets** — the RAG-rescue arm
>    carries the larger ⅔.~~ **SUPERSEDED by rev-5 (the prose probe): the RAG arm does NOT carry the ⅔** —
>    17/24 prose-debt already clear threshold (gate/compose domain, not a retrieval miss). See rev-5
>    changelog + §3b + §14. This bullet stood on an un-probed assumption; the probe refuted it.

> **rev-3 changelog (reviews folded).** (1) §4/§5.4 ordering contradiction RESOLVED — decision **D-ORD =
> (b)**: the LLM rewrite fires AFTER office/deep didn't adopt (cheap-tiers-first); the rare precedence
> risk is owned (§5.4). (2) Dictionary moved to the **top of `handle()`** so it feeds the router, not
> just `_rag_pipeline` (§5.1). (3) **O1 = AUGMENT** (keep the bare acronym + append the expansion) — never
> expand-in-place (IR: GSA is a high-IDF token the bm25 leg lives on). (4) **Structural token-preservation
> guard** added and made MANDATORY (§5.3b) — the answer-gate fed `q2` is circular and cannot backstop a
> drifted rewrite, so a deterministic check is the only fidelity backstop. (5) Gate-2 thread-through now
> includes **line 799** (the Gate-2→live escape). (6) KG-rescue answers **GET 👍/👎/🔄
> buttons** — owner reversed the structured-no-buttons rule 2026-06-29; every answer is now rateable
> (see [[project_open_items]] #10) — and thread `q2` into the compose hint. (7) §3 framing tightened; §8 eval
> expanded (topic-inflation, multi-part, garbled→stay-live).

---

## 1. Problem

The deterministic router (`v2/core/retrieval/router.py`) is exact regex/slot matching with **zero typo or
abbreviation tolerance** — by design ("no LLM here", router.py:10). One typo or a common abbreviation
silently fails to route. Live, confirmed 2026-06-29 via `scripts/ask.sh "heir of cs dep"`:

- `_ROLE_VOCAB_RX` matches literal `chair`; user wrote `heir` → no role match.
- `_DEPT_ENUM` needs literal `department`; user wrote `dep` → no match.
- `route()` → `None`; RAG top cross-encoder **CE=0.012** (miss) → office miss → deep miss → **live web
  search**. The user gets a generic web page instead of the CS chair the KG already holds.

This is the "did you mean…" bottom rung the stack never built (it already has the KG, sqlite-vec, and an
LLM — the upper rungs).

## 2. Goal & non-goals

**Goal.** When a query misses, normalize it (fix typos + expand abbreviations +, for real-word/context
cases, infer the intended word) and **re-route + re-retrieve before the live web fallback**, so a
misspelled/abbreviated *structured* query reaches the KG — **without** corrupting names, dropping question
parts, or mutating queries that already work.

**Non-goals (explicit, loud):**
- **No always-on *LLM* rewrite.** Measured NO-GO (§3): permissive-always-on mutates 93–98% of queries,
  often destructively, with ~zero upside on clean queries. The LLM rewrite fires **only on a confirmed
  miss**. (The deterministic dictionary, §5.1, is a separate safe always-on normalizer — see §3.)
- **No LLM acronym expansion.** The 8B expands `GSA` wrong/inconsistently (§3). Acronyms/abbreviations are
  owned by the **deterministic curated dictionary** (§5.1), never the model.
- **No noisy-channel/statistical corrector** (rev-1 mechanism, dropped — superseded).
- **No whole-sentence reordering / paraphrase / multi-query fan-out.** The rewrite must preserve every
  content token of the question (enforced structurally, §5.3b).
- **No router rule changes.** Correction produces normalized strings and re-invokes the existing decision
  surface + retriever.
- **No new answer source.** Corrected queries are answered by the existing KG/RAG/live paths.

## 3. Evidence (four probes on the live `llama3.1:8b`, 2026-06-29)

Read-only, via Ollama `/api/generate`, temp 0, JSON out. Scripts in the session scratchpad.

- **P1 strict prompt:** safe (never touched a name/topic) but **did NOT fix `heir`** (contextual inference
  forbidden by the prompt). → **strict-always-on is safe-but-useless, and is fully subsumed by the
  deterministic dictionary** (which is the safe always-on normalizer).
- **P2 permissive prompt:** fixed `heir→chair`, generalized (`heir of mechanical eng → chair of mechanical
  engineering`) — but **corrupted a real name** `durvish koutis → "Dhurjati Koutis"`.
- **P3 permissive + KG name-guard:** kept `heir→chair`, **blocked** `durvish→Dhurjati` (reverted),
  accepted legit name-preserving rewrites.
- **P4 always-on measurement** — 793 real logged Qs + 233 clean eval Qs, 5.9 min:

  | Metric | REAL (793) | EVAL clean (233) |
  |---|---|---|
  | latency median / p95 | 0.33s / 0.41s | 0.35s / 0.41s |
  | **change-rate** | **93.4%** | **97.9%** |
  | name-guard reverts | 1.9% | 1.7% |

  Destructive: `what is GSA → "General Student Association"`, `what is the GSA → "General Student
  Assembly"`, `gsa office → "graduate student affairs office"` (correct = **Graduate Student
  Association** — wrong AND inconsistent); `GSA Executive → "gsae"` (typo introduced); `…what vote is
  needed?` dropped (clause lost). The name-guard catches only ~2% (names), not the ~91% acronym/clause/
  scope damage.

**Why always-on (LLM) is rejected (tightened framing):** clean queries already retrieve fine, so an LLM
call on them is **pure latency with ~zero upside** — any nonzero mutation is a strict loss. That cost/
benefit argument (not just the quality damage) is the core reason; the 93–98% mutation rate quantifies the
downside. The data does **not** argue for a different placement. (Permissive-always-on = measured NO-GO;
strict-always-on = subsumed by the dictionary.)

## 3b. Empirical validation — the 1000-question debt run (2026-07-08, the "worth building" proof)

rev 3 was shelved "awaiting owner sign-off" with no hard proof the on-miss LLM tier earned its keep
(the dictionary alone is obviously safe; the LLM rewrite is the risk). The 1000-question DB eval
(`eval/processing_debt/have_answer_matrix.py`, LIVE ON, real pipeline) supplies that proof.

**Method.** Owner's two curated files pre-label the "do we own it?" axis (`Question based on DB.txt` =
should be answerable from KB/KG). Each question ran the real pipeline → classified SURFACED (KB/KG
answered) / LIVE (KB miss → web) / DEBT (deflected, own it) / GAP (don't own it). A "live-owned" flag
marks LIVE answers where a proxy still saw an owned item = **we went to the web on data we hold**.

**Result (1000 DB questions).** SURFACED 56.7% · LIVE 35.6% (**209 on owned data**) · DEBT 5.7% · GAP
2.0%. Own-it-but-missed = **266 (26.6%)**; after removing temporal/real-time + bot-identity noise,
**~183 real fixable debt**.

**Failure MECHANISM (router probe on the real-debt set) — this is the spec's exact target:**
- **97% of real-debt questions get `router.route() == None`** → fall to RAG → miss → live. Not mis-routing;
  NON-routing, from typos/abbreviations/slang. This IS §1's `heir of cs dep` failure, at scale.
- The router-`None` debt splits: **~⅓ STRUCTURED** (role/chair `who run math`/`boss of cs?`, metric
  `top cited prof in computer sci`, research-area `any faculty research hpc?`, club `women in cs how join`,
  dept-count) → the **KG-rescue arm** (§4/§5.2 → `_try_structured(q2)`); **~⅔ PROSE-RAG** (policy/forms:
  `how cpt apply`, `wher submit degreeworks form?`, `cn i take less credits`, `what happen if i miss add
  drop?`) → the **RAG-rescue arm** (`top_relevance(q2) ≥ LIVE_THRESHOLD`).
- Live samples the rewrite directly targets: `wher→where`, `cn→can`, `wht→what`, `profesor→professor`,
  `dedline→deadline`, `heir/boss/run→chair`, `dep→department`.

**⚠️ rev-4 must-fix #4 — the ⅔ PROSE-RAG arm is MUCH SMALLER than "load-bearing" (measured, 2026-07-08).**
Fable flagged that no one had confirmed a typo fix actually lifts prose queries over `LIVE_THRESHOLD`. The
$0 pre-build probe (`scratchpad/qc_prose_probe.py`, 24 prose-debt questions through the real Qwen+CE
retriever) settles it — and the ⅔ claim does NOT hold:
- **17 / 24 prose-debt queries ALREADY clear the threshold WITH the typo** (e.g. `how cpt apply` CE 0.98,
  `what happen if i get academic warning?` 0.96, `i have problem w grade appeal what do` 0.75). Their debt
  was NOT a retrieval miss → the RAG-rescue arm (which fires only on `primary_miss`) never even runs for
  them. Their LIVE/DEBT classification came from DOWNSTREAM (the WS4 answer-gate abstaining / compose) —
  i.e. the **gate/compose domain**, NOT a retrieval miss query-correction fixes. (The gate-2 precision fix
  `cbd4baf` shipped later the same day and addresses part of this, but recovered only 15/39 prose
  false-abstains in its own eval, so a chunk of these 17 likely STILL abstains → the residue is gate/compose
  work, not query-correction's. Optional $0 confirmation: re-run the 17 through the full post-gate-2 pipeline.)
- Only **7 / 24 are genuine retrieval misses**; of those the typo fix converts **5 (71%)** over threshold
  (`where submit tuishon refund form?` 0.03→0.89, `where cn i find info about meal plan` 0.03→0.22, …) and
  2 stay corpus-limited (`what happen if i miss add drop?` 0.07→0.15 borderline; a clean `late payment`
  query at 0.09 = coverage debt no rewrite touches).

**Corrected read.** The RAG-rescue arm is **NOT the ⅔** — most prose debt is a gate/compose issue already
addressed by gate-2. Query-correction's real, exclusive value is: (a) the **acronym dictionary** (safe,
always-on, deterministic); (b) the **~21% of prose debt that ARE typo-induced retrieval misses** (71%
convertible); and (c) the **STRUCTURED ⅓ KG-rescue arm** — routing, NOT retrieval-CE, so untested by this
probe and still the strongest remaining case (`who run cs`/`boss of cs`/`top cited prof in cs`). The
build's success metric is a $0 re-run of the debt harness, but scoped to that reality — and paired with a
correctness audit (must-fix #6), since a SURFACED count alone would reward a topic-inflated wrong-rescue.
`~183` is an ESTIMATE with noise both ways (the have-proxy scans the full corpus incl. `publication`, which
serving excludes → some "owned" items are unservable; structural ownership also under-detected).

## 4. Architecture — dictionary always (pre-router), LLM rewrite on-miss (last resort)

```
top of handle() (after resolve_query → resolved_query):
  q1 = augment_acronyms(resolved_query)     ← §5.1 deterministic dict; AUGMENT (keep original + append
                                              expansion); no-op if nothing matches. Feeds ALL consumers:
                                              Gate-1 _try_structured, UnifiedRouter.decide, the structured
                                              path, and _rag_pipeline's base_q.

inside _rag_pipeline, retrieve on q1 → primary_miss? → office tier → deep fallback (existing)

ON A CONFIRMED MISS, AFTER office/deep did NOT adopt (D-ORD=b):
  q2 = llm_rewrite(q1)                       ← §5.2 permissive, constrained; ~0.33s; the ONLY LLM-rewrite
  q2 = name_guard(q1, q2)                    ← §5.3 revert if a real-name token changed/dropped
  q2 = structure_guard(q1, q2)               ← §5.3b revert if a content token silently dropped / a name
                                               hallucinated-in  (the gate cannot backstop this — §5.5)
  if q2 != q1:
      if (kg = _try_structured(q2)) : answer from KG (with buttons, §5.6)   ← highest precision
      elif top_relevance(q2, retrieve(q2)) >= LIVE_THRESHOLD :
           chunks = retrieve(q2); primary_miss = False; retrieval_q = q2    ← RAG rescue
  ... else existing live njit.edu fallback (unchanged) ...
```

`clean_text` stays ORIGINAL for display/log/history. `retrieval_q` = `q2` on a rescue (else `q1`) and
drives retrieval, the answer-gate CE, **and** the live escape (§5.5). Strictly additive on a miss; the
93% of working queries never reach the LLM (they pass through the deterministic dictionary, §5.1).

## 5. Components

### 5.1 Curated acronym/abbreviation dictionary (NEW; deterministic; top of `handle()`; runs always)
`v2/core/retrieval/acronyms.py`. Applied as `q1 = augment_acronyms(resolved_query)` at the **top of
`handle()`** (right after the context-rewrite, ~`message_handler.py:208`) so q1 feeds Gate-1's
`_try_structured` (:222), `UnifiedRouter.decide` (:236), the structured path (:262), and `_rag_pipeline`'s
`base_q` (:639) — **all four consumers get the same normalized string.** Curated, reviewed, whole-word
map. Owns ALL acronym/abbrev handling (the LLM is forbidden it). Seeds: `gsa → graduate student
association` (the exact thing the 8B got wrong), `dep/dept → department`, `prof → professor`,
`cs → computer science`, `eng → engineering`, `uni → university`.

- **O1 = AUGMENT, not expand-in-place** (RAG-review BLOCKER, IR-decided): emit `original + expansion`
  (`"the sci dept" → "the sci science dept department"`), NOT a replacement. (NOTE: `gsa` itself is NO
  LONGER in the seed map — see the §14.1 HARD EXCLUSION; it's an org slug the router resolves natively and
  expanding it regressed routing. The AUGMENT principle still governs the seeds that remain.) A high-IDF
  token the bm25/FTS leg exact-matches would be dropped by expand-in-place, regressing recall; augment
  preserves the bare token AND adds the expansion to both legs. The
  router's positive-presence regexes tolerate the extra tokens (verified shape: `_ROLE_OF_ORG`/`_find_org`
  match on token presence, not exclusivity).
- **In-vocab / name protection:** never augment a token that is a real corpus term or a `nodes`
  person-name token (so a surname that happens to look like an abbrev is never expanded).
- **Honest clean-path note:** augmenting mutates the routing/retrieval string for *currently-working*
  queries (it ADDS tokens, never drops). Risk is bounded (originals kept) and gated by `eval.sh`
  no-regression; it is NOT "byte-identical," and the doc no longer claims that for the dictionary (only
  the *LLM* path is byte-identical-on-non-miss). **Deferred refinement O1b:** if the eval shows the
  augmented string ever misroutes, split into router-form (expanded slots) vs retriever-form (augmented);
  not needed for v1.

### 5.2 `llm_rewrite(q1)` (NEW; on-miss only)
One constrained Ollama call (model resident; ~0.33s warm). Permissive: fix spelling; if a word clearly
doesn't fit context, replace with the intended word (`heir of cs → chair of computer science`). **Hard
prompt constraints (from P4 failures):** acronyms are ALREADY expanded — do not touch them; never change/
drop/invent a person's name; never turn a research topic into a job title; **preserve every part of the
question**; output JSON `{"rewritten": …}`. Temp 0; `num_predict` sized to query length (a too-small cap
truncates long multi-part queries → §5.3b then reverts them, the safe outcome — noted). Any error/parse-
fail → return `q1` unchanged (never break the path). **The prompt is necessary but NOT sufficient** (P4
proved the 8B violates it anyway) → the two deterministic guards below are the real enforcement.

### 5.3 KG name-guard (NEW)
Load person-name tokens once from `nodes` (`type='Person' AND is_active=1`, tokens len>2 → ~1,795). After
the rewrite, if any ORIGINAL-query token that is a real name token is **absent** from the rewrite → revert
to `q1`. Proven (P3) to block `durvish→Dhurjati` while accepting `heir→chair`. Loaded at module init /
lazy-once (never per-call); refreshed on the crawl/embed/DB-rebuild cadence (a brand-new surname is
unprotected until the next rebuild — bounded, stated).

### 5.3b Structural token-preservation guard (NEW — MANDATORY, RAG-review BLOCKER)
The same microsecond set-intersection, generalized — this is the ONLY backstop for rewrite *fidelity*,
because the answer-gate fed `q2` is circular and cannot catch a drifted rewrite (§5.5). Two rules:
1. **No silent content-token deletion.** A non-stopword token in `q1` that is absent from `q2` is allowed
   ONLY if `q2` contains a token within edit-distance ≤2 of it (a typo fix, e.g. `profesor→professor`).
   Otherwise the rewrite dropped content (a clause) → **revert to `q1`.**
2. **No hallucinated name.** If `q2` introduces a `nodes` name-token that was not in `q1` → **revert**
   (the rewrite invented/substituted a person, e.g. `koutis → Koutsoupias` cases P4 showed).
Topic-*inflation* (`machine learning → machine learning department`, an ADDITION) is NOT caught here — it
is the deferred G2 risk, defensible only because §8's eval now measures the wrong-rescue rate.

### 5.4 Ordering — D-ORD = (b): LLM rewrite AFTER office/deep (both reviewers flagged the contradiction)
The LLM rewrite + KG/RAG rescue fire only **after** the office and deep tiers did not adopt — the true
last resort before live. **Owned tradeoff:** this forfeits a strict KG-precedence guarantee (a weak
office/deep rescue on the typo query could set `primary_miss=False` and pre-empt the higher-precision KG
answer the rewrite would find). Accepted because (i) the same typo that fails the router also poisons
office/deep retrieval (the `heir` case scored CE 0.012 — office/deep miss it too), so the inversion rarely
bites; (ii) (b) keeps the 0.33s LLM off the common miss path when the cheap local tiers already rescue.
This makes §4 and this section agree (rev-2's split was the contradiction). The cheap dictionary (§5.1)
already normalizes abbreviations BEFORE the router/office/deep, so only the contextual real-word fixes
(`heir→chair`) remain for the late LLM tier.

### 5.5 Answer-gate + live thread-through (rev-4 must-fix #2 — invariant, not a site list)
On a RAG-rescue the whole downstream path must judge against `q2`, not the original typo. The rev-3
"four sites at `:776/:777/:781/:799` incl. `is_fact_shaped`" list is DEAD — WS4 (`ANSWER_GATE_ENABLED`,
`c44784f`) **replaced** the pre-generation `is_fact_shaped` gate with a POST-generation
`_faithfulness_gate`, and A15b added a person-scope guard; those anchors no longer exist. So the spec
binds an **INVARIANT**, and the plan re-locates by anchor at build time (line numbers drift):

> **INVARIANT: downstream of the rescue point (where a successful rescue sets `primary_miss=False`
> and `retrieval_q=q2`), EVERY consumer of `base_q` must read `retrieval_q`.**

Today's verified consumers in `_rag_pipeline` (`message_handler.py`, anchors @ 2026-07-08 — re-find by the
CALL, never the number): `top_relevance(base_q, chunks)` primary-miss signal (`:1056`); office/deep
`top_relevance(base_q, …)` (`:1063`, `:1077`); the **primary live escape** `live_search(base_q)` (`:1094`,
correctly pre-empted when the rescue set `primary_miss=False`); the **A15b person-scope guard**
`is_person_seeking(base_q)` + its `live_search(base_q)` (`:1124`, `:1146` — post-dates rev-3, absent from
the rev-3 body); the **WS4 faithfulness gate** `_faithfulness_gate(base_q, …)` (`:1198`, judged against the
prefit context `:1197`); and the **WS4 gate-abstain live escape** `live_search(base_q)` (`:1208`). Compose
already answers `q2` via the `compose_question`/`resolved_query` hint (§5.6), so `:1197` prefit needs no
separate change once that hint is set.

**gate-abstain trigger (rev-4 must-fix #5 — resolved, NOT deferred-ambiguous): NO fresh rewrite on a WS4
gate-abstain for v1.** The 97% router-`None` debt mechanism flows through `primary_miss` → the primary live
escape (`:1094`), which the rescue already intercepts — so the rescue point is correctly placed and needs no
second trigger. On an *already-rescued* query that then WS4-abstains, the ONLY action is to thread `q2` into
the abstain-escape (`:1208`); the build must NOT invent a fresh rewrite→recompose→re-gate loop at that point
(loop/latency risk). A future "rewrite on gate-abstain of a non-rescued query" is a **loud deferred item**
(G5), revisited only if measured.

**Circularity (unchanged):** once retrieval AND the gate use `q2`, the gate only validates "answerable from
chunks the corrected query fetched" — it CANNOT guard rewrite fidelity; §5.3/§5.3b are the only fidelity
backstops. KG-rescue returns BEFORE the gate (structured-exempt, consistent with Gate-1) — so the
highest-confidence tier also rests entirely on §5.3/§5.3b.

### 5.6 KG-rescue response wiring + compose
A KG rescue must build a full `MessageResponse` (since `_try_structured` returns `Optional[str]`, not a
response). **Buttons (owner REVERSED 2026-06-29):** the rescue answer **logs a `question_id` and shows
👍/👎/🔄** — every answer is now rateable ([[feedback_structured_no_buttons]] is reversed; tracker
[[project_open_items]] #10). This aligns with the broader buttons-on-all-answers work (its own gated
build); rev 3 simply does not suppress buttons on the rescue. **Compose split-brain (RA3):** on a RAG-rescue, thread `q2`
into the compose question via the existing `resolved_query`/"(resolved for retrieval: …)" mechanism
(:826-829) so compose answers the same question the chunks were fetched for.

### 5.7 Flag
New on-miss block gated by `QUERY_CORRECT_ENABLED` (default OFF; kill = 0 + restart). The dictionary
(§5.1) ships behind the same flag for v1, promotable to always-on after a shadow window (it's
deterministic/safe). Telemetry: log `original → q1 → q2`, rescue tier (KG/RAG/none), name- and
structure-guard reverts.

## 6. Behavior

| Query | Today | rev 3 |
|---|---|---|
| `heir of cs dep` | None → miss → **web** | dict augments `dep/cs`; router still None (heir); office/deep miss → LLM `heir→chair` (guards ok) → KG `people_by_role(chair, CS)` |
| `who is the chair of cs` (clean) | routes | dict augments `cs` (acronym kept); routes — **LLM never runs** |
| `what is GSA` | RAG | dict `gsa → gsa graduate student association` (bare acronym KEPT) — **LLM never expands it** |
| `durvish koutis` (on a miss) | — | LLM tries "Dhurjati…" → **name-guard reverts** |
| `…what vote is needed?` mistyped | — | if LLM drops the clause → **structure-guard reverts** |
| garbled / paraphrase | RAG → live | LLM may help on miss; else stays live (guards revert a bad rewrite) |

## 7. Deferred / rejected (loudly flagged)

- **G1 — Always-on LLM rewrite: REJECTED** (measured NO-GO, §3). Recorded so it's not re-litigated.
  Revisit only with a materially better model or constrained decoding that *provably* preserves clean
  queries.
- **G2 — Topic-inflation guard** (the one fidelity gap §5.3b doesn't cover). Deferred, but §8 now
  measures the wrong-rescue rate so the deferral is trustworthy; add the guard if the eval shows it bites.
- **G3 — Learned/auto-grown dictionary** from the answered-query log. Curated map only in v1.
- **G4 — Bigram/statistical corrector** — dropped with rev 1.
- **G5 — Fresh rewrite on a WS4 gate-abstain of a NON-rescued query** — deferred (rev-4 must-fix #5, §5.5);
  the 97% mechanism flows through `primary_miss`, already intercepted. Revisit only if measured.
- **G6 — The on-miss `llm_rewrite` + name-guard + structure-guard + shadow apparatus (§§5.2–5.5): DROPPED
  from v1 (rev-5).** Not merely because the RAG arm shrank, but because Finding A proves the guards veto the
  synonym-shaped rewrites that would carry the structured wins. Revivable ONLY if (i) a post-C+A debt
  re-measure shows a material typo-PROSE residue AND (ii) granite passes the §8 min-efficacy shadow bar.
- **O1b — Split router-form vs retriever-form normalization** — only if the augmented string misroutes.

## 8. Testing (TDD)

- **Dictionary**: augments (keeps bare acronym + appends expansion); never augments a corpus term or name
  token; clean query routes the same after augmentation (no-regression).
- **Name-guard (HARD GATE)**: real names — incl. ones **withheld from the fixture `nodes`** — never
  changed; `durvish koutis` reverts; legit name-preserving rewrite accepted.
- **Structure-guard (HARD GATE)**: a rewrite that DROPS a content token reverts (unless an edit-≤2 typo
  fix); a rewrite that INTRODUCES a `nodes` name not in the original reverts; a pure typo fix passes.
- **On-miss-only invariant**: a query that routes/retrieves today never invokes `llm_rewrite`.
- **Gate-after-rescue**: a corrected+rescued query is scored with `q2` and **not deflected**; the
  Gate-2→live escape uses `q2` not the typo (covers line 799).
- **KG-rescue**: returns a full `MessageResponse`, **logs a `question_id` and shows 👍/👎/🔄 buttons**
  (rev-3 reversal, §5.6/§9/§11 — corrected in rev 4; the earlier "no buttons" wording was the leftover
  contradiction), compose gets the `q2` hint.
- **Precedence (D-ORD)**: assert the LLM rewrite fires only after office/deep did not adopt.
- **Eval breadth (RAG-review)**: add — (a) topic typos that SHOULD rescue (`machine lerning researchers`);
  (b) topic-**inflation** adversarial (`machine learning` must NOT inflate + confidently rescue a dept
  page); (c) **multi-part** miss queries (assert no clause drop, or revert); (d) **garbled→stay-live** (an
  unsalvageable query still reaches live, not a forced rescue). These measure the §5.3b + G2 trust.
- **Live smoke (pre-merge, required)**: `ask.sh "heir of cs dep" --answer` → KG CS chair, not web;
  clean-query path unchanged; rewrite latency ≈ 0.33s. Run `eval.sh` → no regression.
- **Shadow measure mode (rev-4 must-fix #3 — TWO-SIDED bar on granite, not one-sided):** corrections
  logged measure-only on real traffic before serving. The §3 P1–P4 rates are **llama3.1-specific**;
  `granite4:tiny-h` is a different, smaller class and MUST be re-measured on BOTH axes:
  - **Max-damage (safety):** destructive-change rate AND — critically — the **addition/topic-inflation
    rate**, the one axis §5.3b does NOT guard (inflation scales with model aggressiveness; a one-sided
    "destructive-only" bar would miss the swap's real risk).
  - **Min-efficacy (capability):** change-rate + rescue-rate on the 183-debt sample, with an explicit
    assertion that the `heir→chair`-class contextual repair works AT ALL. tiny-h may lack it (or default to
    JSON-refuse) → the tier ships silently inert while still costing latency on every miss. A near-0%
    change/rescue rate is a FAIL, not a pass.
  Both `~0.33s` figures in this spec (§5.2, §8 live smoke) are llama3.1 numbers — treat as **re-measure
  targets for granite**, not expectations.

## 9. Hard lines honored

- **No-LLM router preserved**; the LLM touches only the query string on a miss, never the routing rules or
  answer content.
- **Never-withhold / verbatim**: correction changes only the retrieval query; answers come verbatim from
  KG/RAG/live; a failed correction degrades to live exactly as today.
- **Honest-partial / anti-fab**: name-guard + structure-guard + on-miss bound prevent a confident wrong
  rewrite; the dictionary owns acronyms deterministically so GSA is never mis-expanded by the model.
- **Buttons on every answer** (owner reversed 2026-06-29): the KG-rescue answer logs a `question_id`
  and shows 👍/👎/🔄 like all replies ([[project_open_items]] #10).
- **Evidence-before-claim**: grounded in four measured probes; merge needs the live smoke shown.
- **Reversible / gated**: OFF by default behind `QUERY_CORRECT_ENABLED`.

## 10. Goals checklist (close-out — shipped / deferred / rejected)

- G-A — Deterministic acronym dictionary, top-of-`handle()`, AUGMENT, owns acronyms (GSA correct). **SHIP.**
- G-B — On-miss permissive LLM rewrite (typos + contextual inference). **SHIP.**
- G-B2 — RAG re-retrieve rescue arm (`top_relevance(q2) ≥ LIVE_THRESHOLD`). **SHIP** (was folded into
  G-B/E in rev 2; now its own line per review).
- G-C — KG name-guard (revert real-name corruption). **SHIP.**
- G-C2 — Structural token-preservation guard (no clause drop / no hallucinated name). **SHIP** (mandated by
  the gate-circularity finding). 
- G-D — Gate-2 + live escape score with `retrieval_q` (the 4-site fix). **SHIP.**
- G-E — D-ORD=(b): LLM rewrite after office/deep; owned precedence tradeoff. **SHIP.**
- G-F — OFF-by-default flag + shadow FP-measure mode + telemetry. **SHIP.**
- G-G — Always-on LLM rewrite. **REJECTED** (measured, §3).
- G-H — Topic-inflation guard / learned dict / bigram / O1b split-normalization. **DEFERRED** (G2/G3/G4/O1b).
- **Open decisions landed:** O1 = AUGMENT (decided); D-ORD = (b) (decided, owner-confirmable).

## 11. Reject criteria

- The LLM rewrite running on a **non-miss** → reject.
- The **LLM expanding an acronym** (dictionary owns it) → reject.
- **Expand-in-place** dictionary (dropping the bare acronym) → reject (O1).
- Any **real-name corruption** surviving the guard (incl. a withheld-name fixture) → reject.
- A rewrite **dropping a content clause** or **introducing a hallucinated name** passing §5.3b → reject.
- Gate-2 (or the :799 live escape) scoring a rescued query with the original typo (`base_q`) → reject.
- The displayed/logged **original** query replaced by the corrected one → reject.
- KG-rescue answer MISSING feedback buttons (violates the buttons-on-all reversal, owner 2026-06-29) → reject.
- Any `eval.sh` coverage/accuracy regression → reject.

## 12. Build sequence (for writing-plans, after approval)

1. `acronyms.py` — augment map + in-vocab/name protection; wire at top of `handle()` feeding all four
   consumers (tests: augment, never-expand-a-name, clean-route-unchanged).
2. KG name-guard loader + `name_guard()` (tests: revert/accept + withheld-name fixture).
3. `structure_guard()` — content-token-preservation + no-hallucinated-name (tests: clause-drop revert,
   typo-fix pass, introduced-name revert).
4. `llm_rewrite()` constrained call + JSON parse + error→passthrough (tests: stubbed; live smoke).
5. Wire the on-miss block into `_rag_pipeline` AFTER office/deep (D-ORD=b), behind the flag; KG rescue →
   full `MessageResponse` **WITH a logged `question_id` + 👍/👎/🔄 buttons** (owner reversed the
   structured-no-buttons rule 2026-06-29, §5.6/§9/§11); RAG rescue sets `retrieval_q=q2`; preserve original
   (tests: on-miss-only, response-carries-`question_id`-and-buttons, precedence).
6. Thread `retrieval_q=q2` through EVERY downstream `base_q` consumer per the §5.5 INVARIANT (re-locate by
   anchor, not the stale `:799`): the WS4 `_faithfulness_gate`, the A15b person-scope guard, and all three
   `live_search` escapes; compose `q2` hint (tests:
   gate-after-rescue not deflected, live-escape uses q2).
7. Telemetry + shadow measure-only mode (reuse P4 harness).
8. Eval breadth additions (§8) + `eval.sh`; live smoke shown (evidence-before-claim).

## 13. Open questions for the reviewers (rev 3)

1. **D-ORD** — confirm (b) (LLM after office/deep) over (a) (before, strict KG precedence + 0.33s/miss).
2. **Dictionary flag-gated vs always-on for v1** — deterministic + safe; promote immediately or after the
   shadow window?
3. **Rescue threshold** — reuse `LIVE_THRESHOLD` for the RAG re-retrieve adopt, or a dedicated bar?
4. **Structure-guard edit-distance** — is ≤2 the right "this deletion was a typo fix, not a clause drop"
   boundary, or ≤1 + stopword-aware?

---

## 14. rev-5 BUILD SPEC — C+A (dictionary + deterministic router extension) — THE CURRENT SCOPE

> §§4–13 above describe the DEFERRED LLM path (G6). This section is what we BUILD. No LLM anywhere;
> extends the deterministic router (the proven 2026-07-03 pattern). Owner-approved 2026-07-08.

### 14.1 Component A — acronym/abbreviation dictionary (from §5.1, unchanged in intent)
`v2/core/retrieval/query_correct.py`: `augment_acronyms(text, protected=None) -> str`. Curated whole-word,
case-insensitive map; **AUGMENT** (keep the bare token, append the expansion — never expand-in-place, O1).
Applied ONCE at the top of `handle()` (after the context-rewrite) so the augmented string feeds Gate-1
`_try_structured`, `UnifiedRouter.decide`, the structured path, AND `_rag_pipeline`'s `base_q`. Seeds (curated,
reviewed, GSA-equal): `dept/dep→department`, `prof→professor`, `sci→science`, `eng→engineering`,
`uni→university`. **Never augment** a token in `protected` (a `nodes` person-name token or a real corpus term).
Alone this captures the METRIC class (probe: `top cited prof in computer sci` + `sci→science`/`prof→professor`
→ `top_people_by_metric`, no LLM).

> **HARD EXCLUSION (post-build fix, 2026-07-09 — caught by the $0 route-diff gate).** The dictionary MUST
> NOT expand a token the router already resolves as an **org identifier** (slug / alias). Expanding a
> resolvable org slug into its full name (`gsa → gsa graduate student association`, `cs → cs computer
> science`, `ece → …`) BREAKS the router's native org resolution and **demotes a correct structured
> route** (`officers_in_org`, `faculty_in_department`) into RAG. The route-diff over the 324-Q eval showed
> expanding `gsa` silently broke **7** correct GSA officer/president queries. So `gsa`, `cs`, `ece` are
> deliberately DROPPED from the seed map (the router resolves them natively; the org-type LEADER rule in
> §14.2 still handles `who runs GSA` / `who run cs` via native resolution). The dictionary carries ONLY
> generic vocabulary normalizers for tokens the router can't resolve on its own.
>
> **Full invariant (Fable N1/N2), for anyone ADDING a seed later — check the expansion OUTPUT, not just
> the key:** a candidate key is unsafe if (i) it is itself a resolvable org identifier (name/slug/alias);
> **or** (ii) it appears as a whole word in a NON-FINAL position of any multi-word identifier phrase —
> because AUGMENT appends after the token and would split the phrase (a phrase-FINAL occurrence like `sci`
> in the alias `comp sci` is safe: the expansion lands after the whole phrase); **or** (iii) its expansion
> output stitches with adjacent user tokens to form an identifier. Leg (iii) is second-order and can run in
> the user's favour — e.g. `"eng technology faculty"` → `"eng engineering technology faculty"` now matches
> the `engineering technology` alias the raw query missed (a WIN) — but it is the same mechanism that broke
> `gsa`, so verify its DIRECTION per candidate. Locked by the enumerated `test_org_slug_acronyms_are_not_expanded`
> (the three caught tokens) AND the live-DB invariant `test_no_acronym_key_shadows_or_splits_an_org_identifier`
> (legs i–ii + non-final-position of ii), which fails the moment a future org slug/alias collides with a kept key.

### 14.2 Component C — deterministic router extension (`v2/core/retrieval/router.py`)
Three small, native additions. The router ALREADY owns the machinery (`_ROLE_VOCAB_RX` :127, `_ROLE_SYNONYM`
:130, `ORG_TYPE_LEVEL` :133, `ROLE_SCOPE_LEVEL` :144, `_climb_to_scope` :157, `_find_org` :382, the role
branch + `role_is_org` guard :848-872, `_RANK_CUE` :196). Re-locate anchors by symbol at build (numbers drift).

**C-1. Org-type-aware LEADER rule (the 10/10 role win).** A new `_LEADER_INTENT` matcher for leadership-intent
phrasings that are NOT in `_ROLE_VOCAB`: `who runs/run`, `who heads/head of`, `boss of`, `in charge of`,
`who leads`, and `president of <academic unit>`. When it co-occurs with an org resolved by `_find_org`, map to
the org-type-appropriate role FROM THE ORG NODE'S ACTUAL TYPE (not a guess):
  - department → `chair` · college/school → `dean` · university → `president`/`provost` · gsa/club/rgo →
    officer (`officers_in_org`/president).
Route to `people_by_role`/`role_in_org` (or `officers_in_org` for clubs), reusing the existing
`ROLE_SCOPE_LEVEL` + `_climb_to_scope` scoping. **Ordering:** runs in the role region (`:848+`), AFTER the
`_LEADERSHIP_PROCESS` gate (a "how does leadership work" process question still must NOT hit a person lookup)
and composes with the existing `_ROLE_VOCAB_RX` branch. **Disambiguation:** `president`/`provost`/`registrar`
are also office-org names — the org-type resolution settles it (president-of-a-DEPT → chair, not the Office of
the President); keep the `role_is_org` overlap guard (:858) intact so "registrar office hours" → office.

**C-2. Leader-term role synonyms.** Extend `_ROLE_SYNONYM` (:130) so the leader terms normalize to the
resolved role head only when org-type-resolved (never a bare "boss" → chair without a unit).

**C-3. Closed-lexicon edit-≤2 typo tolerance. — ⛔ DEFERRED 2026-07-09 (built, reviewed, BACKED OUT as unsafe).**
The intent: for ROLE / METRIC tokens, Damerau-Levenshtein ≤2 fuzzy-match INTO the closed vocabularies
(`_ROLE_VOCAB`, metric aliases). **Built (bbb246c) + guarded (5cdc021) + reviewed → BACKED OUT to the Task-5
state.** WHY: fuzzing arbitrary query tokens into `_ROLE_VOCAB` — which is full of SHORT, common-word-shaped
entries (`dean`,`chair`,`director`,`registrar`,`cited`) — snaps ORDINARY correctly-spelled words to a role and
silently misroutes common queries, verified live (flag ON): `register for cs`→registrar, `direct me to cs`→
director, `chart of cs`→chair, `koutis cite`→metric (the last also defeats CLAUDE.md's deliberate "bare `cite`
NOT aliased" rule). Two guard rounds (exact-match-wins + first-char) did NOT close the class — a correctly-spelled
unrelated word that shares a first char and lands within DL≤2 of a short vocab entry still fires. The value is
marginal (a handful of typo queries; org-name fuzzing was already deferred as higher-risk), so it does not justify
the fragility. **The pure `closed_lexicon_fix`/`_dl` helper was correct + tested but removed with the wiring (dead
without it).** REVIVE only with a SAFE design: exclude real English words (dictionary/frequency check) AND/OR gate
the typo-fix behind the same person/role-of-org cue the exact-match path requires AND/OR DL≤1 for ≤6-char targets
+ drop short metric aliases. Tracked as **G-C3r (deferred)** in §14.7. The `stdent→student`-class org-name typos
remain unaddressed (they were the org-fuzz path, deferred from the start).

### 14.3 Hard lines / guardrails
- **GSA-equal** ([[feedback_gsa_equal_not_privileged]]): the leader mapping is ORG-TYPE-driven, not a GSA
  thumb; `who runs GSA` → gsa president via the club/officer branch, same mechanism as any unit. No alias table.
- **No-LLM router preserved**; deterministic + zero-latency on the hot path.
- **`role_is_org` + `_LEADERSHIP_PROCESS` guards preserved** (no regression on office-hours / process queries).
- **Run the GOLD/EVAL set, not spot-checks** (the 2026-07-03 lesson): `eval.sh` + the router gold suites must
  show no regression; org-resolution is shared surface.
- **Reversible / gated:** behind `QUERY_CORRECT_ENABLED` (default OFF → shadow → on), kill = `0` + restart.
- Per [[feedback_grow_correctness_suite]]: every new rule adds its Qs to `eval/questions.txt`.

### 14.4 Explicit non-goals (rev-5)
- The `llm_rewrite` + guards + shadow apparatus (G6, deferred).
- The 4 structured non-wins: metric-by-RESEARCH-AREA (`machine learning prof h index?` — needs a new
  metric-over-area skill, not correction) + unresolved CLUBS (`intl student club` — org/entity data gap). Logged,
  not built here.
- Prose corpus-debt residue → the fresh prose-recall track (NOT old-M2).

### 14.5 Verification (TDD)
- Dictionary: augments (keeps bare acronym + appends expansion); never augments a name/corpus term; clean
  query routes the same after augmentation.
- Leader rule: the 10 probe role queries (`who run cs`/`boss of cs`/`who president cs`/…) route to
  `people_by_role` with the CORRECT org + org-type-correct role (dept→chair, college→dean); `boss of ywcc`→dean.
- Metric: `top cited prof in computer sci` (+dict) → `top_people_by_metric`.
- Officer: `women in cs officers who`/`graduate student association officers who` → `officers_in_org`.
- Typo tolerance: `stdent`/`citatns`/`tuishon` map into vocab; a non-vocab typo is left untouched.
- GUARDS: `registrar office hours` → office (role_is_org held); a leadership-PROCESS question not → a person.
- GSA-equal: `who runs GSA` → gsa president; no unit → no bias.
- `eval.sh` + router gold suites: no regression (HARD gate).

### 14.6 Success metric
$0 re-run of the 1000-Q debt harness scoped to the STRUCTURED debt: target = convert a large share of the 16
probe-confirmed router-None structured questions from LIVE/DEBT → SURFACED, PAIRED with a correctness spot-check
of the converted answers (a route alone isn't a correct answer). Report the structured before/after.

### 14.7 Goals checklist (rev-5 — supersedes §10 for the current scope)
- **G-A** — acronym dictionary (AUGMENT, owns acronyms, GSA correct). **SHIP.**
- **G-C1r** — org-type-aware leader rule → people_by_role/officers_in_org. **SHIP.**
- **G-C2r** — leader-term role synonyms (org-type-resolved). **SHIP.**
- **G-C3r** — closed-lexicon edit-≤2 typo tolerance (role/metric). **DEFERRED** (built + reviewed + backed out
  as unsafe on short common-word-shaped vocab; see §14.2 C-3. Revive only with a safe design.)
- **G6** — on-miss LLM rewrite + guards + shadow. **DROPPED/DEFERRED** (Finding A; revivable per §7 G6).
- **§10's G-B/G-B2/G-C/G-C2/G-E** (LLM path) — **SUPERSEDED by G6 deferral**; not in the rev-5 build.

# Fable delta-review — query-correction-salvage rev-4

> Recovered 2026-07-08 from session transcript a4eb3df4fa5cff46c (review was dispatched b3823e5 but never persisted). Verdict: GO-WITH-CHANGES, 6 must-fixes.

All grounding is done — spec read in full, the live `message_handler.py` gate region read, the model config verified (`.env` `OLLAMA_MODEL=granite4:tiny-h`, `ollama_client.py` default matches), and the eval harness (`eval/processing_debt/have_answer_matrix.py`) inspected for its proxy method. Here is the delta review.

---

# Fable delta-review — rev-4 of `docs/superpowers/specs/2026-06-29-query-correction-salvage-design.md`

## 1. Model retarget soundness — MOSTLY SOUND, one uncovered axis + one unmeasured side

The containment argument is correct as far as it goes: both guards are revert-to-`q1`, so the worst fidelity outcome is today's behavior (fall to live). Name corruption, clause drops, and acronym mangling (e.g. granite replacing `gsa` — no edit-distance-2 survivor → structure-guard revert) are all covered regardless of model. JSON refusal / parse-fail → `q1` passthrough is fail-safe per §5.2.

Two things the argument does NOT cover:

- **Topic inflation (G2) scales with model aggressiveness and is the one explicitly unguarded axis** (§5.3b: additions are not caught). llama3.1's P4 numbers bounded that risk; granite's are unknown, and granite4:tiny-h is a *different class* of model (small hybrid, ~1B active) — it could inflate more. The shadow gate as written measures "destructive-change rate" — deletion-shaped damage the guards already catch. **The granite shadow bar must explicitly include the addition/inflation rate**, or the one real risk of the swap goes unmeasured.
- **The unmeasured side is capability, not just safety.** tiny-h may be too weak for the contextual repair (`heir→chair`) that is the entire point of the LLM tier — P2/P3 proved llama3.1 could do it; nothing re-proves granite can. A near-0% change rate (or a JSON-refusal habit) ships a silently inert feature that still costs latency on every miss. The shadow mode needs a **minimum-efficacy measure** (change-rate + rescue-rate on the 183-debt sample), not only a maximum-damage bar. Also, both "~0.33s" figures (§5.2, §8 live smoke) are llama3.1 numbers presented as expectations — relabel as re-measure targets.

Verdict on Q1: the *serving-safety* argument holds; the *shadow re-measure* as specified is one-sided and must be widened.

## 2. The ⅔ PROSE-RAG claim — OVERSOLD as written

§3b says the evidence "confirms BOTH rescue arms are load-bearing." It does not. The evidence confirms the ⅔ are router-None prose questions with messy phrasing. It does **not** confirm that a typo fix lifts them over `LIVE_THRESHOLD` — no one re-ran corrected queries through `top_relevance`. Reasons for doubt, from the codebase itself:

- Several cited samples aren't typo-limited: `how cpt apply` has no typo to fix — `cpt` isn't in the §5.1 seed dictionary, and the rewrite is forbidden from expanding acronyms. The rewrite buys approximately nothing there.
- In `wher submit degreeworks form?`, the content terms (`submit degreeworks form`) are already clean; `wher` is near-stopword. If that query misses today, the cause is plausibly corpus-side — and the project has a **known, open corpus-side cause: M2** (long pages embedded only to ~2000 chars, tracked in `project_m2_embedding.md`). Some unknown fraction of the ⅔ is chunking/embedding debt no rewrite converts.

The fix is cheap and the project's own evidence-before-claim line demands it: **hand-correct 20–30 prose-debt questions and run them through `top_relevance` offline (an hour, $0) before the TDD build.** If conversions are near zero, the build's justification collapses to the ⅓ structured arm and the owner should know that *before* investing. At minimum, §3b's wording must downgrade "confirms load-bearing" to "the RAG arm is the only in-design arm that can address the ⅔; its conversion rate is measured by the before/after."

## 3. WS4 integration — "wiring re-verification" is NOT adequate; the spec's §5.5 is now describing dead code

This is the biggest finding. The drift is larger than flag #4 admits:

- **`is_fact_shaped` no longer exists in `message_handler.py`** — WS4 *replaced* the pre-generation gate with a post-generation faithfulness gate (`_faithfulness_gate` at `:1193`, prefit at `:1192`; the code comment at `:1157` says so explicitly). "Re-locate by anchor" fails when the anchor was deleted. §5.5's four-site list is a description of an architecture that no longer exists.
- The `base_q` thread-through surface has **grown**: there are now three downstream `live_search(base_q)` sites (`:1089` primary — correctly pre-empted by `primary_miss=False`; `:1141` inside the new A15b person-scope guard; `:1203` the WS4 gate-abstain escape), plus the A15b guard's `is_person_seeking(base_q)` at `:1120`, plus the WS4 gate call itself at `:1193`. A rescued query must feed **all** of them `q2`, or the guard/gate judges chunks fetched for `q2` against the typo — reopening exactly the bug §5.5 exists to close.
- **A genuinely new design question WS4 created:** a query that retrieves *above* threshold (`primary_miss=False`), composes, then WS4-abstains, reaches live at `:1203` having **never passed the rescue point**. Should the rewrite also fire on gate-abstain? The rev-4 sentence "a WS4 abstain must still allow the q2 rescue before live" is ambiguous between two readings (thread q2 into the abstain-escape of an already-rescued query — pure wiring; vs. fire a fresh rewrite on gate-abstain — a new trigger with a rewrite→recompose→re-gate loop risk). This is a design decision, not wiring. My recommendation: **NO new trigger for v1** — the 97% mechanism flows through `primary_miss` → live, which the rescue already intercepts — but the spec must say so loudly as a deferred item so the build doesn't improvise it.

Resolve in the spec now: replace the stale 4-site list with the drift-proof invariant — *"downstream of the rescue point, every consumer of `base_q` reads `retrieval_q`"* — plus today's enumerated anchors, and pin the gate-abstain-trigger decision.

## 4. Evidence honesty — harness is honest; §3b is *mostly* faithful, two lapses

The harness file itself is exemplary (explicit proxy caveat, lower-bound framing, structural under-detection). §3b carries most of that over, but:

- **"~183 real fixable debt" is an estimate, not a lower bound.** The harness's lower-bound caveat covers *under*-detection (structural ownership). But the have-proxy also *over*-detects: the semantic probe runs on the full corpus **including publications** (harness docstring, line 6–7), which the serving corpus excludes — some "owned" items are unservable one-paper-title chunks — and `FTS_MIN=3` term-overlap can hit incidentally. Temporal-stripping removed one noise class, not these. One honest sentence fixes it.
- **The before/after is NOT circular** — SURFACED is measured on the ANSWERED axis (the real pipeline answered from KB/KG), independent of the have-proxy. Moving LIVE→SURFACED is a genuine behavioral change. **But it is incomplete:** SURFACED does not check *correctness*. A topic-inflated wrong-rescue (the exact G2 failure) counts as a win under this metric. The success criterion must pair the harness re-run with an accuracy audit of the converted set (spot-judge sample, or `eval.sh` accuracy over the conversions). Without that, the metric structurally rewards the design's own worst failure mode.

## 5. Completeness vs rev-3 goals — one goal-adjacent contradiction SURVIVED the fix that claimed to kill it

- **§12 build-sequence step 5 still says "no buttons" — twice** ("full `MessageResponse`, no buttons" and "(tests: on-miss-only, response/no-buttons, precedence)"). rev-4 changelog #3 claims the buttons contradiction is fixed and corrected §8/§9/§11 — but the build sequence, the exact text writing-plans will execute from, still encodes the rejected behavior. This is precisely how the original contradiction shipped. Must fix.
- G-B (contextual LLM rewrite) is silently weakened by the swap until granite's capability is shown — covered by must-fix 3 below. G-B2's load-bearing claim — covered by must-fix 4. No other §10 goal is broken.

---

## VERDICT: **GO-WITH-CHANGES**

The core rev-4 moves are right — config-driven model per the LLM-agnostic line, real evidence folded in, guards as the safety story. But three of the five delta claims are softer than presented, and one contradiction the delta claims to have fixed is still in the file. Must-fix before writing-plans:

1. **§12 step 5 — remove "no buttons" (both occurrences); bind buttons-ON + `question_id` test**, matching the corrected §8/§9/§11. (The contradiction rev-4 #3 claims fixed survives in the build sequence — the highest-risk place for it to survive.)
2. **§5.5 — rewrite the thread-through as an invariant, not a site list**: "downstream of the rescue point, every `base_q` consumer reads `retrieval_q`," with today's anchors enumerated: WS4 `_faithfulness_gate`/`prefit` (`message_handler.py:1192-1193`), the gate-abstain live escape (`:1203`), the A15b person-scope guard (`:1120` `is_person_seeking`, `:1141` live) — and note `is_fact_shaped` is deleted, so flag #4's "re-locate by anchor" is impossible as written. (A15b post-dates rev 3 and is absent from the spec entirely.)
3. **Rev-4 #1 / §8 shadow mode — widen the granite bar to both directions**: (a) destructive-rate AND addition/inflation-rate (the one unguarded axis, which scales with model aggressiveness), (b) a minimum-efficacy measure (change-rate + rescue-rate on the 183-debt sample; assert `heir→chair`-class repair works at all — tiny-h may simply lack it, shipping an inert tier). Relabel both "~0.33s" figures as re-measure targets.
4. **§3b — downgrade "confirms BOTH arms load-bearing" and add the $0 pre-build probe**: hand-correct 20–30 prose-debt questions, run `top_relevance` vs `LIVE_THRESHOLD` offline before TDD. If conversion is ~0, the ⅔ is corpus debt (M2 chunking, acronym gaps like `cpt` that neither dictionary-seed nor rewrite touches) and the build case narrows to the structured ⅓ — the owner should see that number first.
5. **Rev-4 #4 — resolve the gate-abstain trigger question in the spec now**: recommend NO fresh rewrite on WS4 gate-abstain for v1 (the 97% mechanism flows through `primary_miss`, already intercepted), stated as a loud deferred item; and disambiguate the current sentence so the build threads `q2` into an already-rescued query's abstain-escape without inventing a rewrite→recompose→re-gate loop.
6. **§3b success metric — pair the harness re-run with a correctness audit of converted answers** (SURFACED alone rewards topic-inflated wrong-rescues), and add one sentence that ~183 is an estimate with noise in both directions (have-proxy includes publications the serving corpus excludes; structural ownership under-detected).

None of these require a third full review round — they are spec edits plus one hour of offline probing. With them applied, this is ready for owner sign-off and the TDD build.
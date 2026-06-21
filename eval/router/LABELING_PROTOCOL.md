# Router labeling protocol (Kavosh v2.1)

The bake-off decides an architecture, so the GOLD/TEST set must be trustworthy. Two reviews
(RAG/IR-eval + ML/annotation) converged on this protocol. Train data may be LLM-proposed; **gold
test data must be labeled BLIND** so the proposer's prior can't silently become the test answer.

## Roles of the data
- `provenance:"seed"` — synthetic rows. **Train only** (`dataset.py` refuses `seed` + `split:test`).
- `provenance:"real"`, `split:"train"` — harvested rows labeled by LLM-propose → human-confirm. Fine for train.
- `provenance:"real"`, `split:"test"` — the GOLD set. Labeled **blind** (below).
- `split:"hardneg"` — boundary/adversarial suite (follow-ups, multilingual, RAG-vs-LIVE, OTHER). Scored separately.

## Building the gold test set (blind)
1. `python scripts/router_make_gold_stubs.py 60` → `eval/router/gold_stubs.jsonl` (rows with `family:"?"`).
2. **Mohammad labels each stub blind** — fill `family` (+ `skill`/`source`/`slots`) WITHOUT seeing any proposed route.
3. (Separately, hidden) the LLM proposes a family per id → a proposals map.
4. `merge_blind_labels(human_rows, proposals, annotator="mohammad")` records `proposed_family` + `confirmed`
   (did the blind label match the proposal?) and stamps `provenance:real, split:test`. Append to `labeled_routes.jsonl`.

## Trust gates (run before scaling)
- **IAA:** double-label a stratified ~25–30 row sample (2nd annotator = different model, or Mohammad on
  another day, proposals hidden). `cohen_kappa(family_a, family_b) ≥ 0.8`. Below → tighten the rubric
  (esp. CLARIFY / OTHER / RAG-vs-LIVE) and re-label.
- **Canary audit:** `inject_canaries(proposals, frac=0.05, …)` plants deliberate errors into a propose→confirm
  review batch. Correction rate on canaries must be ~100%; otherwise the review is rubber-stamping.
- **Edit-rate:** `edit_rate(rows)` over a review batch should be non-trivial (10–30%+) on harvested noise.
  Near-zero = rubber-stamp red flag.

## Targets
≥100 real `split:test` rows, ≥10–20 per family that exists in traffic; ≥2–3 distinct phrasings per
decision-relevant KG skill (several are at n≤1 now). Mark under-mass skills out-of-eval rather than
silently averaging them into headline accuracy.

## Re-run
`python scripts/router_bakeoff.py` — once a `split:test` gold set exists the bake-off uses it directly
(seeds pinned to train), reporting entity-disjoint (primary) + paraphrase splits with honesty notes.

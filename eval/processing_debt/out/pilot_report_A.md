# Processing-Debt Pilot Report — Set A

**Processing Debt (demand-weighted): 73.8%** (95% CI 61–85%, cluster-bootstrap over 37 questions) — 121 owned-misses / 164 vital owned facts

## Per-stage (owned-misses)

| Stage | Count |
|---|---|
| ROUTER | 0 |
| POOL | 114 |
| RANK | 2 |
| COMPOSE | 5 |
| CONFIG | 0 |
| UNRESOLVED | 0 |

## Sample size (power analysis)

- Observed yield: 4.43 owned-vital facts/question over 37 questions.
- CI note: percentile bootstrap over 37 questions; approximate at the tails for n≈50 (use BCa if a tighter tail is needed)
- For ±5% overall: ~216 questions needed (cluster-consistent; naive fact count 297).
- For ±10% overall: ~54 questions needed (cluster-consistent; naive fact count 74).
  - POOL: 114 misses (94% of misses) → ~6 questions for ±10% on its share.
  - RANK: 2 misses (2% of misses) → ~3 questions for ±10% on its share.
  - COMPOSE: 5 misses (4% of misses) → ~5 questions for ±10% on its share.

## Instrument validity (Cohen's κ)


## Success criteria
- SC1 (κ≥0.6 both decisions): FAIL
- SC4 (≥5 owned-misses): PASS
- SC5 (≥70% attributed): PASS
- SC6 (oracle-incorrectness ≤30%): PASS (rate 26% = 40 unsupported + 18 we-are-authority / 222 guarded)
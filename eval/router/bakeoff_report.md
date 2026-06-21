# Kavosh v2.1 — Phase-0 Bake-off Report

train/test: 36/15 (seed 0)

## detector_first
- family_accuracy: 0.667
- skill_accuracy: 1.0
- structured_false_negative: 2
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.800
- skill_accuracy: 1.0
- structured_false_negative: 2
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.933
- skill_accuracy: 0.0
- structured_false_negative: 0
- false_honest_partial: 2  (anti-fab)
- wrong_confident_exact: 8  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

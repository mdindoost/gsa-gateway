# Phase-0 Bake-off — entity-disjoint (PRIMARY) split

split: entity-disjoint | train/test: 89/24 (seed 0)

## detector_first
- family_accuracy: 0.792
- skill_accuracy: 0.9473684210526315
- structured_false_negative: 5
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.625
- skill_accuracy: 1.0
- structured_false_negative: 9
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.708
- skill_accuracy: 0.35294117647058826
- structured_false_negative: 7
- false_honest_partial: 2  (anti-fab)
- wrong_confident_exact: 11  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.750
- skill_accuracy: 0.9444444444444444
- structured_false_negative: 6
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.833
- skill_accuracy: 0.6
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 8  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.833
- skill_accuracy: 0.6
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 8  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}


---

# Phase-0 Bake-off — paraphrase-disjoint split

split: paraphrase-disjoint | train/test: 80/33 (seed 0)

## detector_first
- family_accuracy: 0.697
- skill_accuracy: 0.9285714285714286
- structured_false_negative: 5
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.697
- skill_accuracy: 0.9230769230769231
- structured_false_negative: 6
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.667
- skill_accuracy: 0.47058823529411764
- structured_false_negative: 2
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 9  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.727
- skill_accuracy: 0.9285714285714286
- structured_false_negative: 5
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.758
- skill_accuracy: 0.6111111111111112
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.758
- skill_accuracy: 0.6111111111111112
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

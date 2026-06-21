# Phase-0 Bake-off — entity-disjoint (PRIMARY) split

split: entity-disjoint | train/test: 113/101 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=101): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.

## detector_first
- family_accuracy: 0.554
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.634
- skill_accuracy: 0.95
- structured_false_negative: 20
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.752
- skill_accuracy: 0.7575757575757576
- structured_false_negative: 7
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 8  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.653
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.792
- skill_accuracy: 0.8378378378378378
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 6  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.792
- skill_accuracy: 0.8378378378378378
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 6  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}


---

# Phase-0 Bake-off — paraphrase-disjoint split

split: paraphrase-disjoint | train/test: 113/101 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=101): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.

## detector_first
- family_accuracy: 0.554
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.634
- skill_accuracy: 0.95
- structured_false_negative: 20
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.752
- skill_accuracy: 0.7575757575757576
- structured_false_negative: 7
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 8  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.653
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.792
- skill_accuracy: 0.8378378378378378
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 6  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.792
- skill_accuracy: 0.8378378378378378
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 6  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

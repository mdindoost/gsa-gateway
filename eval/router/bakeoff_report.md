# Phase-0 Bake-off — entity-disjoint (PRIMARY) split

split: entity-disjoint | train/test: 113/28 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=28): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.

## detector_first
- family_accuracy: 0.607
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.714
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse
- family_accuracy: 0.679
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full_abstain
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}


---

# Phase-0 Bake-off — paraphrase-disjoint split

split: paraphrase-disjoint | train/test: 113/28 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=28): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.

## detector_first
- family_accuracy: 0.607
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.714
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse
- family_accuracy: 0.679
- skill_accuracy: 1.0
- structured_false_negative: 3
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full_abstain
- family_accuracy: 0.750
- skill_accuracy: 1.0
- structured_false_negative: 1
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 0  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

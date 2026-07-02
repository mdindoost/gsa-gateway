# Phase-0 Bake-off — entity-disjoint (PRIMARY) split

split: entity-disjoint | train/test: 422/97 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=97): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.
> - family abstention active: calibrated margin=0.0783 on VAL.

## detector_first
- family_accuracy: 0.577
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 17
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.670
- skill_accuracy: 0.9047619047619048
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.784
- skill_accuracy: 0.78125
- structured_false_negative: 7
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.649
- skill_accuracy: 0.95
- structured_false_negative: 19
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse_kgbias
- family_accuracy: 0.660
- skill_accuracy: 0.9047619047619048
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse_balanced
- family_accuracy: 0.649
- skill_accuracy: 0.95
- structured_false_negative: 19
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.794
- skill_accuracy: 0.8
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.794
- skill_accuracy: 0.8
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse_abstain
- family_accuracy: 0.443
- skill_accuracy: 0.9333333333333333
- structured_false_negative: 24
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}


---

# Phase-0 Bake-off — paraphrase-disjoint split

split: paraphrase-disjoint | train/test: 422/97 (seed 0)

> NOTES (read before trusting the numbers):
> - coarse_* arms get their SKILL from the deterministic router (which resolves entities against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and the deterministic arms enjoy a DB entity oracle the classifier arms do not.
> - small N (test=97): single-digit anti-fab counts drive the gate; one row can flip a verdict — treat deltas as directional, not significant.
> - abstention inactive (not needed): TRAIN skill precision already meets target at full coverage, so margin=0.0 and masked_full_abstain == masked_full.
> - family abstention active: calibrated margin=0.0783 on VAL.

## detector_first
- family_accuracy: 0.577
- skill_accuracy: 0.9090909090909091
- structured_false_negative: 17
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'reason': 'baseline'}

## coarse_then_deterministic
- family_accuracy: 0.670
- skill_accuracy: 0.9047619047619048
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## full_classifier
- family_accuracy: 0.784
- skill_accuracy: 0.78125
- structured_false_negative: 7
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse
- family_accuracy: 0.649
- skill_accuracy: 0.95
- structured_false_negative: 19
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse_kgbias
- family_accuracy: 0.660
- skill_accuracy: 0.9047619047619048
- structured_false_negative: 18
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 2  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_coarse_balanced
- family_accuracy: 0.649
- skill_accuracy: 0.95
- structured_false_negative: 19
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

## masked_full
- family_accuracy: 0.794
- skill_accuracy: 0.8
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_full_abstain
- family_accuracy: 0.794
- skill_accuracy: 0.8
- structured_false_negative: 4
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 7  (anti-fab)
- gate: {'rejected': True, 'reason': 'anti-fab leak above detector-first baseline'}

## masked_coarse_abstain
- family_accuracy: 0.443
- skill_accuracy: 0.9333333333333333
- structured_false_negative: 24
- false_honest_partial: 0  (anti-fab)
- wrong_confident_exact: 1  (anti-fab)
- gate: {'rejected': False, 'reason': 'ok'}

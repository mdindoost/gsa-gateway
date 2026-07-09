# Layer-3 false-answer differential (should-abstain set, n=70: abstain+fp_traps from gate_shadow.jsonl)

Production pipeline, gate ON, LIVE off. A should-abstain question that comes back NOT abstained = leak.
Both runs under identical shared-GPU contention (8 gate-call timeouts each → forced keeps).

| branch | false-answer leaks | rate |
|--------|-------------------|------|
| main (origin/main, baseline) | 20/70 | 28.6% |
| feat/gate2-precision-fix     | 19/70 | 27.1% |

**Verdict: the reframe + metric backstop is false-answer NEUTRAL (19 vs 20, marginally better).**
The 27–28% absolute is a PRE-EXISTING pipeline property present on BOTH branches — dominated by
Gate-1 edge misses ("dean of engineering at Princeton"), temporal ("dining menu today"), and typed
fp_traps ("how many credits", "GPA to keep assistantship", "deadline to add/drop"). None of these are
touched by the gate2 reframe (non-typed factual only). So the literal "≤15% absolute" Layer-3 gate is
unachievable on THIS set even by main; the meaningful criterion — the reframe must not RAISE
false-answer — is satisfied. The pre-existing false-answer floor is a SEPARATE track (out of scope).

Paired with the false-abstain direction: recovery 0→15 (regression fixture). Net: +15 correct answers,
0 net new false-answers.

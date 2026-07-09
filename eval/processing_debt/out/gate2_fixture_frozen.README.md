# gate2_fixture_frozen.jsonl — provenance

Built by `eval/processing_debt/build_gate2_fixture.py` (LIVE_ENABLED=0), 2026-07-08.
47 cases re-captured through the live pipeline (granite4:tiny-h; generate_answer default temp 0.3, matching production). Drift vs
`prose_gate_diag.jsonl`: 0 (rank-1 retrieval stable → hand-labels still valid).

Controller re-adjudication of boundary cases (#4/#43/#45), full captured answers:
- #4  "can freshman work off campus?"  abstain -> KEEP  (full answer gives the grounded
      off-campus rule: "before working off-campus you must obtain authorization from OGI";
      original abstain label was set from a truncated ai[:160] that cut off the off-campus span)
- #43 "i20 sign where go"              abstain (unchanged; answer drifts to Form I-515A, rel=0.22)
- #45 "where submit scholarship form?" abstain (unchanged; answer self-admits "not explicitly
      mentioned", no grounded submission location)

Final: 39 keep (must-recover) / 8 abstain (guardrail, must-still-abstain).
Layer-2 thresholds derive from THIS file (all abstain must abstain; keeps >= n_keep-4).

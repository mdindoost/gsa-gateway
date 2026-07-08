import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import pytest
from eval.processing_debt.types import FactRecord, PresenceResult
from eval.processing_debt.gate import (
    evaluate_control_gate, enforce_control_gate, require_oracle_reachable,
    verify_embedding_alignment, GateResult)


def _rec(cls, stratum, vital=True, stage=None):
    return FactRecord("q", stratum, "f", vital, "supported", cls == "IN_ANSWER",
                      PresenceResult(cls != "NOT_OWNED", [], []), cls, stage, "q")


def test_sc2_passes_at_zero_or_one_positive_miss():
    recs = [_rec("IN_ANSWER", "positive_control"),
            _rec("OWNED_NOT_SURFACED", "positive_control", stage="POOL")]   # exactly 1 miss
    res = evaluate_control_gate(recs)
    assert res.sc2_pass is True and res.positive_owned_misses == 1

def test_sc2_fails_at_two_positive_misses():
    recs = [_rec("OWNED_NOT_SURFACED", "positive_control", stage="POOL"),
            _rec("OWNED_NOT_SURFACED", "positive_control", stage="RANK")]
    res = evaluate_control_gate(recs)
    assert res.sc2_pass is False and res.positive_owned_misses == 2 and res.reasons

def test_sc3_passes_when_all_blind_flagged():
    recs = [_rec("DROPPED_ORACLE", "oracle_blind"), _rec("DROPPED_ORACLE", "oracle_blind")]
    res = evaluate_control_gate(recs)
    assert res.sc3_pass is True and res.oracle_blind_total == 2 and res.oracle_blind_flagged == 2

def test_sc3_fails_when_a_blind_fact_not_flagged():
    recs = [_rec("DROPPED_ORACLE", "oracle_blind"), _rec("IN_ANSWER", "oracle_blind")]
    res = evaluate_control_gate(recs)
    assert res.sc3_pass is False and res.reasons

def test_enforce_calls_exit_on_failure():
    recs = [_rec("OWNED_NOT_SURFACED", "positive_control", stage="POOL")] * 3   # SC2 fail
    called = {}
    def fake_exit(msg): called["msg"] = msg; raise SystemExit(2)
    with pytest.raises(SystemExit):
        enforce_control_gate(recs, exit_fn=fake_exit)
    assert "SC2 FAIL" in called["msg"]

def test_enforce_returns_result_on_pass():
    recs = [_rec("IN_ANSWER", "positive_control"), _rec("DROPPED_ORACLE", "oracle_blind")]
    res = enforce_control_gate(recs, exit_fn=lambda m: (_ for _ in ()).throw(AssertionError("should not halt")))
    assert isinstance(res, GateResult) and res.passed is True

def test_require_oracle_reachable_halts_when_probe_false():
    hit = {}
    require_oracle_reachable(probe=lambda: True, exit_fn=lambda m: hit.setdefault("bad", m))
    assert "bad" not in hit                                   # reachable → no halt
    require_oracle_reachable(probe=lambda: False, exit_fn=lambda m: hit.setdefault("bad", m))
    assert "M6 FAIL" in hit["bad"]                            # unreachable → halt

def test_verify_embedding_alignment_halts_on_empty_hits():
    ok = {}
    verify_embedding_alignment(None, embed_knn=lambda: [(1, "policy", "text")],
                               exit_fn=lambda m: ok.setdefault("bad", m))
    assert "bad" not in ok                                    # hits → aligned
    verify_embedding_alignment(None, embed_knn=lambda: [],
                               exit_fn=lambda m: ok.setdefault("bad", m))
    assert "EMBED ALIGN FAIL" in ok["bad"]                    # 0 hits → halt (likely missing .env)

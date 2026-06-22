"""eval.sh accuracy gate (accuracy backlog #4) — the pass/fail threshold logic.

Spec: the gate is opt-in (thresholds via --min-answered/--min-correct); default report-only.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from eval_report import gate_result   # importable (report body is under __main__)


def test_gate_inert_when_no_thresholds():
    passed, lines = gate_result(answered_pct=50, correct_pct=50, min_answered=None, min_correct=None)
    assert passed is True and lines == []          # report-only → never fails


def test_gate_passes_when_above_floors():
    passed, lines = gate_result(90, 82, 85, 80)
    assert passed is True


def test_gate_fails_when_correct_below():
    passed, lines = gate_result(90, 70, 85, 80)
    assert passed is False and any("correct" in l.lower() and "FAIL" in l for l in lines)


def test_gate_fails_when_answered_below():
    passed, lines = gate_result(80, 95, 85, 80)
    assert passed is False and any("answered" in l.lower() and "FAIL" in l for l in lines)


def test_gate_at_exact_floor_passes():
    passed, _ = gate_result(85, 80, 85, 80)        # >= floor passes
    assert passed is True

import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.run_pilot import njit_scope

def test_scope_appends_njit_when_absent():
    assert njit_scope("who is the CS chair") == "who is the CS chair at NJIT (New Jersey Institute of Technology)"
    assert njit_scope("who is the CS chair?").endswith("at NJIT (New Jersey Institute of Technology)")

def test_scope_noop_when_already_njit():
    assert njit_scope("who is the CS chair at NJIT") == "who is the CS chair at NJIT"
    assert njit_scope("New Jersey Institute of Technology admissions") == "New Jersey Institute of Technology admissions"

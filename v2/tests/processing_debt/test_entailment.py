import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.entailment import entail_verdict, entails

def test_verdict_yes():
    assert entail_verdict("Pan Xu is an Assistant Professor.", "Pan Xu, Assistant Professor of CS.",
                          gen=lambda s, p, sc: {"verdict": "yes"}) == "yes"

def test_verdict_unsure_passthrough():
    assert entail_verdict("X won an award.", "X is a professor.",
                          gen=lambda s, p, sc: {"verdict": "unsure"}) == "unsure"

def test_verdict_failsafe_no_on_model_failure():
    assert entail_verdict("x", "y", gen=lambda s, p, sc: None) == "no"

def test_verdict_failsafe_no_on_garbage():
    assert entail_verdict("x", "y", gen=lambda s, p, sc: {"verdict": "maybe"}) == "no"

def test_entails_true_only_on_yes():
    assert entails("a", "b", gen=lambda s, p, sc: {"verdict": "yes"}) is True
    assert entails("a", "b", gen=lambda s, p, sc: {"verdict": "unsure"}) is False
    assert entails("a", "b", gen=lambda s, p, sc: {"verdict": "no"}) is False

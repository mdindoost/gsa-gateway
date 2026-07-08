"""NLI cross-encoder judge (Xenova/nli-deberta-v3-base via onnxruntime + tokenizers).

B4 (Fable gate): the encoding is premise=SPAN, hypothesis=FACT. These tests PIN that
direction — reversing the pair must change the score — so the direction can never invert
silently. Uses the REAL model (already in models/nli/); each test loads once (~1-2s).
"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.nli_judge import NliJudge

HI, LO = 0.5, 0.35


def test_genuine_span_supports_fact_scores_high():
    j = NliJudge()
    scores = j.score("NJIT is located in Newark, New Jersey.",
                     ["New Jersey Institute of Technology is a public research university in Newark, New Jersey."])
    assert scores is not None and scores[0] >= HI


def test_unrelated_pair_scores_low():
    j = NliJudge()
    scores = j.score("He joined the program in September 2022.",
                     ["Prof. Tat-Seng Chua is a Professor at the School of Computing, "
                      "National University of Singapore."])
    assert scores is not None and scores[0] < LO


def test_encoding_direction_is_span_then_fact():
    """B4: span entails fact one-directionally (span is more specific). Scoring (span=>fact)
    must be HIGH; scoring the reverse (fact as premise, span as hypothesis) must be LOWER.
    This asserts the asymmetry so a swapped-argument bug is caught."""
    j = NliJudge()
    span = "NJIT is a public research university located in Newark, New Jersey, enrolling over 12000 students."
    fact = "NJIT is in Newark."
    forward = j.score(fact, [span])[0]          # premise=span, hypothesis=fact  -> entailed
    reverse = j.score(span, [fact])[0]          # premise=fact, hypothesis=span  -> NOT fully entailed
    assert forward >= HI
    assert forward > reverse


def test_batch_returns_one_score_per_span_in_order():
    j = NliJudge()
    spans = [
        "New Jersey Institute of Technology is in Newark, New Jersey.",   # supports
        "Prof. Tat-Seng Chua is a professor at NUS.",                     # unrelated
        "NJIT is a public research university in Newark.",                # supports
    ]
    scores = j.score("NJIT is located in Newark.", spans)
    assert scores is not None and len(scores) == 3
    assert scores[0] >= HI and scores[2] >= HI
    assert scores[1] < LO


def test_empty_spans_returns_empty_list():
    assert NliJudge().score("anything", []) == []


def test_failsafe_returns_none_on_unloadable_model():
    j = NliJudge(model_dir=Path("/nonexistent/nli/model/dir"), allow_download=False)
    assert j.score("a fact", ["a span"]) is None

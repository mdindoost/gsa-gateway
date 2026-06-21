from v2.eval.router.types import LabeledExample, RoutePrediction
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.abstain import AbstainingArm, calibrate_thresholds


class _Inner:
    def __init__(self, p):
        self.p = p

    def predict(self, q):
        return self.p


def test_abstaining_arm_routes_low_margin_to_clarify():
    high = RoutePrediction("KG", "faculty_in_department", score=0.9, margin=0.5)
    low = RoutePrediction("KG", "faculty_in_department", score=0.9, margin=0.01)
    assert AbstainingArm(_Inner(high), margin_min=0.1).predict("x").family == "KG"
    out = AbstainingArm(_Inner(low), margin_min=0.1).predict("x")
    assert out.family == "CLARIFY" and out.margin == 0.01


def test_abstaining_passes_through_when_no_confidence():
    p = RoutePrediction("RAG", source="general")        # score/margin None
    assert AbstainingArm(_Inner(p), margin_min=0.9).predict("x").family == "RAG"


def test_calibrate_returns_valid_thresholds():
    enc = FakeEncoder(16)
    ex = [LabeledExample("1", "free pizza today", "RAG", source="food"),
          LabeledExample("2", "who teaches cs", "KG", "faculty_in_department")]
    clf = ExemplarClassifier(level="family").fit(ex, enc)
    score_min, margin_min = calibrate_thresholds(clf, ex, enc, level="family", target_precision=0.9)
    assert score_min == 0.0 and margin_min >= 0.0

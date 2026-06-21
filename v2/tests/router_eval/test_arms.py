from v2.eval.router.types import LabeledExample, RoutePrediction
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.arms import FullClassifierArm, from_route


class _FakeRoute:           # mimics v2.core.retrieval.router.Route
    def __init__(self, skill, args): self.skill = skill; self.args = args


def test_from_route_maps_skill():
    p = from_route(_FakeRoute("people_in_org", {"org_id": 3}))
    assert p.family == "KG" and p.skill == "people_in_org"


def test_from_route_none_is_rag():
    p = from_route(None)
    assert p.family == "RAG"


def test_full_classifier_arm_predicts_family_and_skill():
    enc = FakeEncoder(16)
    ex = [LabeledExample("1", "free pizza today", "RAG", source="food"),
          LabeledExample("2", "who teaches in cs", "KG", "faculty_in_department")]
    clf = ExemplarClassifier(level="skill").fit(ex, enc)
    arm = FullClassifierArm(clf, enc)
    p = arm.predict("any free food this week")
    assert isinstance(p, RoutePrediction) and p.family in ("RAG", "KG")
    assert p.score is not None and p.margin is not None

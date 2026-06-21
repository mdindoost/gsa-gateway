from v2.eval.router.types import Family, RoutePrediction, LabeledExample


def test_family_constants():
    assert Family.KG == "KG" and Family.RAG == "RAG"
    assert set(Family.ALL) == {"KG", "RAG", "LIVE", "CLARIFY", "COMMAND", "OTHER"}
    assert Family.OTHER == "OTHER"


def test_prediction_and_example_defaults():
    p = RoutePrediction(family="KG", skill="people_in_org")
    assert p.source is None and p.slots == {} and p.score is None
    e = LabeledExample(id="x1", query="who is the dean of CS", family="KG", skill="people_by_role")
    assert e.group is None and e.slots == {}

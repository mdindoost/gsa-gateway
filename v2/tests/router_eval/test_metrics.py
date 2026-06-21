from v2.eval.router.types import LabeledExample, RoutePrediction
from v2.eval.router.metrics import score


def _ex(skill=None, family="KG", source=None):
    return LabeledExample("x", "q", family, skill, source)


def test_family_and_skill_accuracy():
    pairs = [(_ex("people_in_org"), RoutePrediction("KG", "people_in_org")),
             (_ex("people_in_org"), RoutePrediction("KG", "officers_in_org"))]
    r = score(pairs)
    assert r["family_accuracy"] == 1.0
    assert r["skill_accuracy"] == 0.5
    assert r["wrong_confident_exact"] == 1     # second pair: KG X vs KG Y


def test_structured_false_negative():
    pairs = [(_ex("top_people_by_metric"), RoutePrediction("RAG", source="general"))]
    r = score(pairs)
    assert r["structured_false_negative"] == 1


def test_false_honest_partial():
    # gold is a roster ask, predicted as a terminal metric/link skill -> would assert "I don't have X"
    pairs = [(_ex("faculty_in_department"), RoutePrediction("KG", "metric_of_person"))]
    r = score(pairs)
    assert r["false_honest_partial"] == 1


def test_false_honest_partial_on_non_kg_gold():
    # the dangerous leak: a non-KG question routed confidently to a terminal person skill ->
    # would fabricate "I don't have <person>'s citations". Must be counted even though gold is RAG.
    pairs = [(_ex(skill=None, family="RAG", source="general"), RoutePrediction("KG", "metric_of_person")),
             (_ex(skill=None, family="OTHER"), RoutePrediction("KG", "link_of_person"))]
    r = score(pairs)
    assert r["false_honest_partial"] == 2


def test_terminal_gold_terminal_pred_is_not_fhp():
    # gold legitimately asks for a metric and we predicted the metric skill -> NOT a false honest-partial
    pairs = [(_ex("metric_of_person"), RoutePrediction("KG", "metric_of_person"))]
    r = score(pairs)
    assert r["false_honest_partial"] == 0

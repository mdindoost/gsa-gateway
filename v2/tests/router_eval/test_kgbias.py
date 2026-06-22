from types import SimpleNamespace
from v2.eval.router.arms import kg_recall_bias


def _route(skill):
    return lambda conn, q: SimpleNamespace(skill=skill, args={"org": "cs"})


def test_prefers_kg_when_close_and_resolvable():
    ranked = [("RAG", 0.80), ("KG", 0.78)]
    out = kg_recall_bias(ranked, _route("faculty_in_department"), None, "who teaches cs", margin_max=0.05)
    assert out is not None and out.family == "KG" and out.skill == "faculty_in_department"


def test_noop_when_router_resolves_nothing():
    ranked = [("RAG", 0.80), ("KG", 0.78)]
    assert kg_recall_bias(ranked, lambda c, q: None, None, "x", margin_max=0.05) is None


def test_noop_when_kg_margin_too_wide():
    ranked = [("RAG", 0.90), ("KG", 0.50)]
    assert kg_recall_bias(ranked, _route("x"), None, "x", margin_max=0.05) is None


def test_noop_when_top_is_not_rag():
    ranked = [("KG", 0.80), ("RAG", 0.78)]
    assert kg_recall_bias(ranked, _route("x"), None, "x", margin_max=0.05) is None

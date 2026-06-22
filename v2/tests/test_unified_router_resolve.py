from types import SimpleNamespace
from v2.core.retrieval.unified_router import UnifiedRouter


def test_kg_route_resolves_to_skill(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route",
                        lambda c, q: SimpleNamespace(skill="faculty_in_department", args={"org_id": 5}))
    r = UnifiedRouter(db_path=":memory:", classifier=None, intent_detector=None)
    d = r.resolve_kg("who teaches cs")
    assert d.family == "KG" and d.skill == "faculty_in_department" and d.args == {"org_id": 5}


def test_kg_empty_falls_to_rag(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route", lambda c, q: None)
    r = UnifiedRouter(db_path=":memory:", classifier=None, intent_detector=None)
    d = r.resolve_kg("how do I become a dean")     # a negative guard makes route() return None
    assert d.family == "RAG" and d.source == "general"

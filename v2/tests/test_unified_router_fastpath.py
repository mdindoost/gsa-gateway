from types import SimpleNamespace
from v2.core.retrieval.unified_router import UnifiedRouter


class _ClfBoom:
    def ranked(self, q): raise AssertionError("classifier must NOT be consulted on a fast-path hit")
    def top(self, q): raise AssertionError("no encode on fast-path")


class _NoCmd:
    def detect(self, m): return ("question", 0.9)


def test_fast_path_hits_without_classifier(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route",
                        lambda c, q: SimpleNamespace(skill="faculty_in_department", args={"org_id": 1}))
    r = UnifiedRouter(":memory:", _ClfBoom(), intent_detector=_NoCmd())
    d = r.decide("list the cs faculty")          # strong structured cue → fast path
    assert d.family == "KG" and d.skill == "faculty_in_department"


def test_non_structured_falls_through_to_classifier(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route", lambda c, q: None)
    class _Clf:
        def ranked(self, q): return [("RAG", 0.9), ("KG", 0.1)]
        def top(self, q): return ("RAG", 0.9, 0.8)
    r = UnifiedRouter(":memory:", _Clf(), intent_detector=_NoCmd())
    d = r.decide("tell me something interesting")
    assert d.family == "RAG"

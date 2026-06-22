from types import SimpleNamespace
from v2.core.retrieval.unified_router import UnifiedRouter, RouteDecision
from bot.services.intent_detector import IntentDetector


class _Clf:
    def __init__(self, ranked): self._r = ranked
    def ranked(self, q): return self._r
    def top(self, q):
        r = self._r
        m = r[0][1] - (r[1][1] if len(r) > 1 else 0.0)
        return (r[0][0], r[0][1], m)


def test_command_wins_first():
    r = UnifiedRouter(":memory:", _Clf([("KG", 0.9), ("RAG", 0.1)]), IntentDetector())
    assert r.decide("hi").family == "COMMAND"


def test_kg_family_resolves(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route",
                        lambda c, q: SimpleNamespace(skill="officers_in_org", args={"org_id": 2}))
    r = UnifiedRouter(":memory:", _Clf([("KG", 0.9), ("RAG", 0.1)]), IntentDetector())
    d = r.decide("who are the gsa officers")
    assert d.family == "KG" and d.skill == "officers_in_org"


def test_rag_top_becomes_general(monkeypatch):
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route", lambda c, q: None)
    r = UnifiedRouter(":memory:", _Clf([("RAG", 0.9), ("KG", 0.1)]), IntentDetector())
    d = r.decide("what is the constitution about")
    assert d.family == "RAG" and d.source == "general"


def test_clarify_top_degrades_to_rag(monkeypatch):
    # Abstention is BUILT-but-OFF in Phase 1b, so a classifier CLARIFY must behave EXACTLY like
    # RAG (never a rephrase prompt that's worse than today's RAG). [flip-gate review, 2026-06-22]
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route", lambda c, q: None)
    r = UnifiedRouter(":memory:", _Clf([("CLARIFY", 0.9), ("RAG", 0.1)]), IntentDetector())
    d = r.decide("what is the constitution about")
    assert d.family == "RAG" and d.source == "general"

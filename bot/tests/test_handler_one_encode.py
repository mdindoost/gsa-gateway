"""E1 invariants: (a) ONE classify-encode per decide() on a non-fast-path message; (b) ANTI-FAB —
a deterministic KG route routed through _answer_decision must NOT call ollama.compose_from_rows
(numbers/links are never reworded) and the deterministic suffix is appended verbatim."""
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
import pytest
import numpy as np

from bot.core.message_handler import MessageHandler, MessageRequest
from v2.core.retrieval.route_classifier import RouteClassifier
from v2.core.retrieval.unified_router import UnifiedRouter


class _Ident:
    def mask(self, q): return q


class _NoCmd:
    def detect(self, m): return ("question", 0.9)


def test_one_classify_encode_per_decide(monkeypatch):
    # route() returns None so a KG-top decision degrades to RAG without a second encode.
    import v2.core.retrieval.router as sr
    monkeypatch.setattr(sr, "route", lambda c, q: None)
    calls = {"n": 0}

    def enc(texts):
        calls["n"] += 1
        # distinct vectors so RAG exemplar wins for the query (avoids the KG branch entirely)
        rows = []
        for t in texts:
            rows.append([1.0, 0.0] if "story" in t else [0.0, 1.0])
        return np.array(rows)

    clf = RouteClassifier([("tell me a story", "RAG"), ("cs faculty", "KG")], enc, _Ident())
    calls["n"] = 0                       # reset: ignore the fit-time encode
    r = UnifiedRouter(":memory:", clf, _NoCmd())
    d = r.decide("please tell me an interesting story")   # no fast-path cue, not a command
    assert calls["n"] == 1               # exactly ONE classify encode
    assert d.family == "RAG"


def _kg_handler(compose_return):
    h = MessageHandler.__new__(MessageHandler)
    for a in ("retriever", "conversation_manager", "intent_detector", "db",
              "rate_limiter", "kb", "config", "unified_router"):
        setattr(h, a, None)
    h.ollama = MagicMock()
    h.ollama.compose_from_rows = AsyncMock(return_value=compose_return)
    return h


@pytest.mark.asyncio
async def test_deterministic_kg_route_skips_llm_compose(monkeypatch):
    h = _kg_handler("LLM REWORDED NUMBERS")
    monkeypatch.setattr(h, "_structured_from_route",
                        lambda skill, args: ("Citations: 1234", "🔗 Scholar", True), raising=False)
    decision = SimpleNamespace(family="KG", skill="metric_of_person", args={},
                               source=None, command_intent=None)
    out = await h._answer_decision(
        MessageRequest(user_id="u", text="koutis citations", platform="discord"), decision)
    h.ollama.compose_from_rows.assert_not_called()          # numbers NEVER reworded
    assert out.text == "Citations: 1234\n\n🔗 Scholar"       # suffix appended verbatim


@pytest.mark.asyncio
async def test_nondeterministic_kg_route_uses_llm_compose(monkeypatch):
    h = _kg_handler("Nice grounded prose")
    monkeypatch.setattr(h, "_structured_from_route",
                        lambda skill, args: ("raw facts", "", False), raising=False)
    decision = SimpleNamespace(family="KG", skill="faculty_in_department", args={"org_id": 1},
                               source=None, command_intent=None)
    out = await h._answer_decision(
        MessageRequest(user_id="u", text="who teaches cs", platform="discord"), decision)
    h.ollama.compose_from_rows.assert_called_once()
    assert out.text == "Nice grounded prose"

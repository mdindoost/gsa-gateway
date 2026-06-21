import asyncio
from unittest.mock import MagicMock, patch
from bot.core.message_handler import MessageHandler, MessageRequest


def _handler(unified):
    h = MessageHandler.__new__(MessageHandler)
    h.retriever = None; h.ollama = None; h.conversation_manager = None
    h.intent_detector = None; h.db = None; h.rate_limiter = None; h.kb = None; h.config = None
    h.unified_router = unified
    return h


def test_shadow_logs_but_does_not_change_answer(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", True, raising=False)
    monkeypatch.setattr("bot.config.ROUTER_V21_SHADOW", True, raising=False)
    unified = MagicMock()
    unified.decide.return_value = MagicMock(family="KG", skill="faculty_in_department")
    h = _handler(unified)
    with patch.object(h, "_try_structured", return_value=None), \
         patch.object(h, "_rag_pipeline", return_value="RAG-ANSWER"), \
         patch("bot.core.message_handler.log_shadow") as logsh:
        # minimal stubs for the pre-structured guards
        h.conversation_manager = MagicMock(); h.conversation_manager.get_mode.return_value = "gsa"
        monkeypatch.setattr("bot.core.message_handler.parse_explicit_live_search", lambda t: None)
        h.intent_detector = MagicMock(); h.intent_detector.detect.return_value = ("question", 0.9)
        out = asyncio.get_event_loop().run_until_complete(
            h.handle(MessageRequest(user_id="u", text="who teaches cs", platform="discord")))
    assert logsh.called                      # shadow logged
    unified.decide.assert_called_once()       # new router consulted
    # answer still from the existing path (rag pipeline), unchanged by shadow
    assert out == "RAG-ANSWER"

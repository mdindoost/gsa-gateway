"""F1: shadow must capture the LEGACY decision too, so new-vs-current agreement (flip-gate Clause 2)
is actually computable — not just a histogram of the new decision."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from bot.core.message_handler import MessageHandler, MessageRequest


def _h():
    h = MessageHandler.__new__(MessageHandler)
    for a in ("retriever", "ollama", "db", "rate_limiter", "kb", "config", "unified_router"):
        setattr(h, a, None)
    h.conversation_manager = MagicMock(); h.conversation_manager.get_mode.return_value = "gsa"
    h.intent_detector = MagicMock(); h.intent_detector.detect.return_value = ("question", 0.9)
    return h


@pytest.mark.asyncio
async def test_legacy_family_free_mode_is_rag():
    h = _h(); h.conversation_manager.get_mode.return_value = "free"
    with patch.object(h, "_try_structured", new=AsyncMock(return_value="STRUCT")):
        assert await h._legacy_family("who teaches cs", "u") == "RAG"   # free skips structured


@pytest.mark.asyncio
async def test_legacy_family_structured_is_kg():
    h = _h()
    with patch.object(h, "_try_structured", new=AsyncMock(return_value="roster text")):
        assert await h._legacy_family("list cs faculty", "u") == "KG"


@pytest.mark.asyncio
async def test_legacy_family_command_intent():
    h = _h()
    h.intent_detector.detect.return_value = ("greeting", 0.9)
    with patch.object(h, "_try_structured", new=AsyncMock(return_value=None)):
        assert await h._legacy_family("hi", "u") == "COMMAND"


@pytest.mark.asyncio
async def test_legacy_family_falls_to_rag():
    h = _h()
    with patch.object(h, "_try_structured", new=AsyncMock(return_value=None)):
        assert await h._legacy_family("tell me about the constitution", "u") == "RAG"


@pytest.mark.asyncio
async def test_shadow_record_includes_current_family_and_agree(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", True, raising=False)
    monkeypatch.setattr("bot.config.ROUTER_V21_SHADOW", True, raising=False)
    h = _h()
    h.unified_router = MagicMock()
    h.unified_router.decide.return_value = MagicMock(family="KG", skill="faculty_in_department")
    with patch.object(h, "_legacy_family", new=AsyncMock(return_value="RAG")), \
         patch.object(h, "_try_structured", new=AsyncMock(return_value=None)), \
         patch.object(h, "_rag_pipeline", new=AsyncMock(return_value="RAG-ANSWER")), \
         patch("bot.core.message_handler.parse_explicit_live_search", lambda t: None), \
         patch("bot.core.message_handler.log_shadow") as logsh:
        await h.handle(MessageRequest(user_id="u", text="who teaches cs", platform="discord"))
    rec = logsh.call_args[0][0]
    assert rec["new_family"] == "KG" and rec["current_family"] == "RAG"
    assert rec["agree"] is False        # KG (new) vs RAG (legacy) → disagreement captured

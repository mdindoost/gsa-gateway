import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse


@pytest.mark.asyncio
async def test_act_kg_returns_structured(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", True, raising=False)
    monkeypatch.setattr("bot.config.ROUTER_V21_SHADOW", False, raising=False)
    h = MessageHandler.__new__(MessageHandler)
    for a in ("retriever", "ollama", "conversation_manager", "intent_detector", "db",
              "rate_limiter", "kb", "config"):
        setattr(h, a, None)
    h.conversation_manager = MagicMock(); h.conversation_manager.get_mode.return_value = "gsa"
    h.unified_router = MagicMock()
    h.unified_router.decide.return_value = MagicMock(family="KG", skill="officers_in_org",
                                                     args={"org_id": 1})
    with patch.object(h, "_answer_decision",
                      new=AsyncMock(return_value=MessageResponse(text="STRUCTURED"))) as ad, \
         patch("bot.core.message_handler.parse_explicit_live_search", lambda t: None):
        out = await h.handle(
            MessageRequest(user_id="u", text="who are the gsa officers", platform="discord"))
    assert out.text == "STRUCTURED" and ad.called

"""ACT-mode precedence + free-mode invariants (review F2 / F3)."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse


def _act_handler(decision):
    h = MessageHandler.__new__(MessageHandler)
    for a in ("retriever", "ollama", "intent_detector", "db", "rate_limiter", "kb", "config"):
        setattr(h, a, None)
    h.conversation_manager = MagicMock(); h.conversation_manager.get_mode.return_value = "gsa"
    h.unified_router = MagicMock(); h.unified_router.decide.return_value = decision
    return h


@pytest.mark.asyncio
async def test_explicit_live_wins_over_act(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", True, raising=False)
    monkeypatch.setattr("bot.config.ROUTER_V21_SHADOW", False, raising=False)
    # the router would classify it KG, but an explicit "search njit for X" must win deterministically
    h = _act_handler(MagicMock(family="KG", skill="faculty_in_department", args={}))
    with patch("bot.core.message_handler.parse_explicit_live_search", lambda t: "parking permits"), \
         patch.object(h, "_answer_explicit_live",
                      new=AsyncMock(return_value=MessageResponse(text="LIVE-ANSWER"))) as live, \
         patch.object(h, "_answer_decision",
                      new=AsyncMock(return_value=MessageResponse(text="DECISION"))) as dec:
        out = await h.handle(MessageRequest(user_id="u", text="search njit for parking permits",
                                            platform="discord"))
    assert out.text == "LIVE-ANSWER"
    live.assert_called_once()
    dec.assert_not_called()              # ACT decision must NOT have run

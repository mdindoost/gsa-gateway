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


@pytest.mark.asyncio
async def test_act_kg_in_free_mode_goes_to_rag_not_structured():
    # In free (general chat) mode a KG decision must NOT run the GSA structured path — it goes to
    # the RAG pipeline (which handles free mode). Preserves the "free skips structured" invariant.
    h = _act_handler(MagicMock(family="KG", skill="faculty_in_department", args={"org_id": 1},
                               source=None, command_intent=None))
    h.conversation_manager.get_mode.return_value = "free"
    called = {"structured": False}
    def _no_structured(skill, args):
        called["structured"] = True
        return ("facts", "", False)
    with patch.object(h, "_structured_from_route", side_effect=_no_structured), \
         patch.object(h, "_rag_pipeline",
                      new=AsyncMock(return_value=MessageResponse(text="FREE-RAG"))) as rag:
        out = await h._answer_decision(
            MessageRequest(user_id="u", text="who teaches cs", platform="discord"),
            h.unified_router.decide.return_value)
    assert out.text == "FREE-RAG"
    rag.assert_called_once()
    assert called["structured"] is False        # structured path skipped in free mode


@pytest.mark.asyncio
async def test_act_kg_in_gsa_mode_runs_structured():
    h = _act_handler(MagicMock(family="KG", skill="faculty_in_department", args={"org_id": 1},
                               source=None, command_intent=None))
    h.conversation_manager.get_mode.return_value = "gsa"
    h.ollama = MagicMock(); h.ollama.compose_from_rows = AsyncMock(return_value="composed")
    with patch.object(h, "_structured_from_route", side_effect=lambda s, a: ("facts", "", False)):
        out = await h._answer_decision(
            MessageRequest(user_id="u", text="who teaches cs", platform="discord"),
            h.unified_router.decide.return_value)
    assert out.text == "composed"               # structured path ran in gsa mode

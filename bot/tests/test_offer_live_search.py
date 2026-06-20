"""_rag_pipeline sets MessageResponse.offer_live_search correctly.

The offer is the contextual "want me to search NJIT's website?" affordance. It rides a
confident-deflection signal (an answer composed from chunks that reads answered but punts the
user elsewhere). It must be suppressed when the feature is off, when we already answered live
(used_live), or when this turn already tried live and got nothing (attempted_live).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.config as botcfg
import bot.core.message_handler as mh
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


def _chunk(text="The library has study spaces."):
    return SimpleNamespace(
        item_id=1, source_file="gsa.md", section_title="Library",
        relevance_score=0.5, text=text,
    )


@pytest.fixture
def handler():
    cm = MagicMock()
    cm.get_mode.return_value = "gsa"
    cm.get_history.return_value = []
    cm.get_session.return_value = None
    retriever = AsyncMock()
    retriever.retrieve.return_value = [_chunk()]
    retriever.top_relevance = MagicMock(return_value=0.5)   # above floor → no auto-fire
    ollama = AsyncMock()
    config = MagicMock(); config.conversation_max_turns = 5
    rl = MagicMock(); rl.is_allowed.return_value = True
    return MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=cm,
        intent_detector=MagicMock(), db=MagicMock(), rate_limiter=rl,
        kb=MagicMock(), config=config,
    )


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)


async def _ask(handler, q="what are the current library hours please"):
    return await handler._rag_pipeline(MessageRequest(user_id="u1", text=q, platform="telegram"),
                                       q, INTENT_QUESTION)


@pytest.mark.asyncio
async def test_deflection_answer_from_chunks_offers_live_search(handler, live_on):
    handler.ollama.generate_answer.return_value = (
        "The library has study spaces. For current hours, see library.njit.edu."
    )
    resp = await _ask(handler)
    assert resp.offer_live_search is True


@pytest.mark.asyncio
async def test_factual_answer_does_not_offer(handler, live_on):
    handler.ollama.generate_answer.return_value = "The library is open weekdays 8AM to midnight."
    resp = await _ask(handler)
    assert resp.offer_live_search is False


@pytest.mark.asyncio
async def test_offer_suppressed_when_feature_off(handler, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "")
    handler.ollama.generate_answer.return_value = (
        "The library has study spaces. For current hours, see library.njit.edu."
    )
    resp = await _ask(handler)
    assert resp.offer_live_search is False


@pytest.mark.asyncio
async def test_offer_suppressed_when_answered_live(handler, live_on, monkeypatch):
    # No chunks → auto-fire path; live returns an answer → used_live, no offer.
    handler.retriever.retrieve.return_value = []
    monkeypatch.setattr(
        mh, "maybe_answer_live",
        AsyncMock(return_value=SimpleNamespace(text="From NJIT's website: open 24h.",
                                               source_url="https://library.njit.edu")),
    )
    resp = await _ask(handler)
    assert resp.offer_live_search is False


@pytest.mark.asyncio
async def test_offer_suppressed_when_live_attempted_but_empty(handler, live_on, monkeypatch):
    # No chunks → auto-fire ran, returned None → don't offer to redo a search that just failed.
    handler.retriever.retrieve.return_value = []
    monkeypatch.setattr(mh, "maybe_answer_live", AsyncMock(return_value=None))
    resp = await _ask(handler)
    assert resp.offer_live_search is False

"""handle() short-circuits an explicit 'search njit for X' to a direct live search.

It wins BEFORE the structured KG router and the RAG pipeline (the user explicitly asked for
the live web). The live answer is logged with a question_id (normal feedback buttons), or the
shared 'searched, found nothing' message when the search comes back empty.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.message_handler import MessageHandler, MessageRequest
from bot.core.live_query import LIVE_NOT_FOUND_MSG
from bot.services.intent_detector import INTENT_QUESTION


@pytest.fixture
def handler():
    rl = MagicMock(); rl.is_allowed.return_value = True
    cm = MagicMock(); cm.get_mode.return_value = "gsa"
    intent = MagicMock(); intent.detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(
        retriever=AsyncMock(), ollama=AsyncMock(), conversation_manager=cm,
        intent_detector=intent, db=MagicMock(), rate_limiter=rl,
        kb=MagicMock(), config=MagicMock(),
    )
    # never let the structured router run for these tests
    h._try_structured = AsyncMock(return_value=None)
    return h


@pytest.mark.asyncio
async def test_explicit_search_returns_live_answer(handler):
    answer = SimpleNamespace(text="From NJIT's website: open 24h.", source_url="https://library.njit.edu")
    handler.live_search = AsyncMock(return_value=answer)
    resp = await handler.handle(MessageRequest(user_id="u1", text="search njit for library hours", platform="telegram"))
    assert "open 24h" in resp.text
    handler.live_search.assert_awaited_once_with("library hours")
    # explicit search wins before the structured router
    handler._try_structured.assert_not_called()
    # source rendered once: carried on source_note, NOT re-embedded by the handler
    assert resp.source_note == "https://library.njit.edu"
    assert resp.is_live is True


@pytest.mark.asyncio
async def test_explicit_search_empty_uses_shared_not_found(handler):
    handler.live_search = AsyncMock(return_value=None)
    resp = await handler.handle(MessageRequest(user_id="u1", text="search njit for unicorn rentals", platform="telegram"))
    assert resp.text == LIVE_NOT_FOUND_MSG


@pytest.mark.asyncio
async def test_normal_question_does_not_trigger_live_search(handler):
    handler.live_search = AsyncMock()
    handler._rag_pipeline = AsyncMock(return_value=MagicMock(text="normal"))
    await handler.handle(MessageRequest(user_id="u1", text="who is the dean of YWCC", platform="telegram"))
    handler.live_search.assert_not_called()

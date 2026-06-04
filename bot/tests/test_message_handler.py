"""Tests for the platform-agnostic MessageHandler."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_QUESTION,
    INTENT_THANKS,
)


@pytest.fixture
def mock_services():
    rate_limiter = MagicMock()
    rate_limiter.is_allowed.return_value = True

    intent_detector = MagicMock()
    intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)

    conversation_manager = MagicMock()
    conversation_manager.get_session.return_value = None
    conversation_manager.get_history.return_value = []

    config = MagicMock()
    config.conversation_max_turns = 5

    return {
        "retriever": AsyncMock(),
        "ollama": None,
        "conversation_manager": conversation_manager,
        "intent_detector": intent_detector,
        "db": MagicMock(),
        "rate_limiter": rate_limiter,
        "kb": MagicMock(),
        "config": config,
    }


@pytest.fixture
def handler(mock_services):
    return MessageHandler(**mock_services)


@pytest.mark.asyncio
async def test_rate_limited_returns_wait_message(handler):
    handler.rate_limiter.is_allowed.return_value = False
    req = MessageRequest(user_id="123", text="hello", platform="discord")
    resp = await handler.handle(req)
    assert "wait" in resp.text.lower() or "too quickly" in resp.text.lower()
    assert resp.source_note is None
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_greeting_no_history_returns_full_intro(handler):
    handler.intent_detector.detect.return_value = (INTENT_GREETING, 0.95)
    handler.conversation_manager.get_session.return_value = None
    req = MessageRequest(user_id="123", text="hi", platform="telegram")
    resp = await handler.handle(req)
    assert "gsa gateway" in resp.text.lower()
    assert "njit" in resp.text.lower()


@pytest.mark.asyncio
async def test_greeting_with_history_returns_short_welcome(handler):
    handler.intent_detector.detect.return_value = (INTENT_GREETING, 0.95)
    session = MagicMock()
    session.turns = [MagicMock(), MagicMock()]
    handler.conversation_manager.get_session.return_value = session
    req = MessageRequest(user_id="123", text="hi again", platform="discord")
    resp = await handler.handle(req)
    assert "welcome back" in resp.text.lower()


@pytest.mark.asyncio
async def test_thanks_returns_acknowledgment(handler):
    handler.intent_detector.detect.return_value = (INTENT_THANKS, 0.9)
    req = MessageRequest(user_id="123", text="thanks!", platform="discord")
    resp = await handler.handle(req)
    assert any(
        word in resp.text.lower()
        for word in ("welcome", "glad", "happy", "help")
    )


@pytest.mark.asyncio
async def test_clear_history_clears_session(handler):
    handler.intent_detector.detect.return_value = (INTENT_CLEAR_HISTORY, 0.9)
    req = MessageRequest(user_id="123", text="clear", platform="discord")
    resp = await handler.handle(req)
    handler.conversation_manager.clear_session.assert_called_once_with("123")
    assert "clear" in resp.text.lower() or "fresh" in resp.text.lower()


@pytest.mark.asyncio
async def test_help_returns_command_list(handler):
    handler.intent_detector.detect.return_value = (INTENT_HELP, 0.9)
    req = MessageRequest(user_id="123", text="help", platform="telegram")
    resp = await handler.handle(req)
    assert "/events" in resp.text or "events" in resp.text.lower()


@pytest.mark.asyncio
async def test_question_no_chunks_returns_fallback(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    handler.retriever.retrieve = AsyncMock(return_value=[])
    req = MessageRequest(user_id="123", text="what is gsa?", platform="discord")
    resp = await handler.handle(req)
    assert "gsa-pres@njit.edu" in resp.text or "contact" in resp.text.lower()
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_question_chunks_no_ollama_returns_chunk_text(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards for grad students."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = None
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert "gsa provides travel awards" in resp.text.lower()
    assert resp.source_note is not None
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_question_with_ollama_returns_ai_response(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = AsyncMock()
    handler.ollama.generate_answer = AsyncMock(
        return_value="GSA provides travel awards for presenting at conferences."
    )
    handler.ollama.expand_query = AsyncMock(return_value=None)
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert resp.text == "GSA provides travel awards for presenting at conferences."
    assert resp.used_ai is True


@pytest.mark.asyncio
async def test_ollama_failure_sets_ollama_failed(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = AsyncMock()
    handler.ollama.generate_answer = AsyncMock(return_value=None)  # Ollama down
    handler.ollama.expand_query = AsyncMock(return_value=None)
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert resp.ollama_failed is True
    assert not resp.used_ai
    assert "gsa provides travel awards" in resp.text.lower()


@pytest.mark.asyncio
async def test_logs_question_to_db(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    handler.retriever.retrieve = AsyncMock(return_value=[])
    req = MessageRequest(user_id="999", text="what is gsa?", platform="discord", guild_id=42)
    await handler.handle(req)
    handler.db.log_question.assert_called_once()
    call_kwargs = handler.db.log_question.call_args.kwargs
    assert call_kwargs["guild_id"] == 42

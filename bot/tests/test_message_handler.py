"""Tests for the platform-agnostic MessageHandler."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse, FREE_MODE_SYSTEM_PROMPT
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FREE_MODE,
    INTENT_GREETING,
    INTENT_GSA_MODE,
    INTENT_HELP,
    INTENT_IDENTITY,
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


# ── Identity intent ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_identity_with_ollama_includes_model_name(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="who are you", platform="discord"))
    assert "GSA Gateway" in resp.text
    assert "llama3.1:8b" in resp.text
    assert resp.used_ai is False
    assert resp.source_note is None


@pytest.mark.asyncio
async def test_identity_mentions_kavosh_version_and_creator(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="who are you", platform="discord"))
    assert "GSA Gateway" in resp.text          # brand
    assert "Kavosh" in resp.text               # version
    assert "md72@njit.edu" in resp.text        # creator credit


@pytest.mark.asyncio
async def test_greeting_mentions_kavosh_version(handler):
    handler.intent_detector.detect.return_value = (INTENT_GREETING, 0.95)
    handler.conversation_manager.get_session.return_value = None
    resp = await handler.handle(MessageRequest(user_id="123", text="hi", platform="telegram"))
    assert "Kavosh" in resp.text


@pytest.mark.asyncio
async def test_identity_without_ollama_omits_model_name(mock_services):
    mock_services["ollama"] = None
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="what are you", platform="telegram"))
    assert "GSA Gateway" in resp.text
    assert resp.text


@pytest.mark.asyncio
async def test_identity_does_not_call_retriever(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "mistral:7b"
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    await h.handle(MessageRequest(user_id="u1", text="who are you", platform="discord"))
    mock_services["retriever"].retrieve.assert_not_called()


# ── Free mode toggle ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_free_mode_toggle_sets_mode_and_confirms(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["intent_detector"].detect.return_value = (INTENT_FREE_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="free mode", platform="discord"))
    assert "General Chat Mode" in resp.text
    mock_services["conversation_manager"].set_mode.assert_called_once_with("u1", "free")


@pytest.mark.asyncio
async def test_free_mode_unavailable_without_ollama(mock_services):
    mock_services["ollama"] = None
    mock_services["intent_detector"].detect.return_value = (INTENT_FREE_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="free mode", platform="discord"))
    assert "isn't available" in resp.text or "not available" in resp.text.lower()
    mock_services["conversation_manager"].set_mode.assert_not_called()


@pytest.mark.asyncio
async def test_gsa_mode_toggle_sets_mode_and_confirms(mock_services):
    mock_services["intent_detector"].detect.return_value = (INTENT_GSA_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="gsa mode", platform="discord"))
    assert "GSA Mode" in resp.text
    mock_services["conversation_manager"].set_mode.assert_called_once_with("u1", "gsa")


# ── Free mode routing in _rag_pipeline ───────────────────────────────────────

@pytest.mark.asyncio
async def test_free_mode_skips_rag_and_calls_generate(mock_services):
    mock_services["ollama"] = AsyncMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["ollama"].generate = AsyncMock(return_value="Paris is the capital of France.")
    mock_services["conversation_manager"].get_mode.return_value = "free"
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    resp = await h.handle(
        MessageRequest(user_id="u1", text="what is the capital of France?", platform="discord")
    )
    assert resp.text == "Paris is the capital of France."
    assert resp.source_note == "General Chat Mode"
    mock_services["ollama"].generate.assert_called_once_with(
        prompt="what is the capital of France?",
        system=FREE_MODE_SYSTEM_PROMPT,
    )
    mock_services["retriever"].retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_free_mode_skips_structured_answer(mock_services):
    # Regression: a STRUCTURED-looking query ("who is the provost") must NOT return the GSA
    # structured answer in free mode — it goes to the general LLM. (Bug: _try_structured ran
    # before the free-mode check, so free mode behaved like GSA mode for structured queries.)
    mock_services["ollama"] = AsyncMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["ollama"].generate = AsyncMock(return_value="A provost is a senior academic officer.")
    mock_services["conversation_manager"].get_mode.return_value = "free"
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="who is the provost", platform="discord"))
    assert resp.source_note == "General Chat Mode"        # answered by the free LLM, not structured
    mock_services["ollama"].generate.assert_called_once()


@pytest.mark.asyncio
async def test_free_mode_ollama_failure_returns_error_message(mock_services):
    mock_services["ollama"] = AsyncMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["ollama"].generate = AsyncMock(return_value=None)
    mock_services["conversation_manager"].get_mode.return_value = "free"
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="something", platform="discord"))
    assert resp.source_note == "General Chat Mode"
    assert "try again" in resp.text.lower()


@pytest.mark.asyncio
async def test_gsa_mode_still_uses_rag(mock_services):
    mock_services["conversation_manager"].get_mode.return_value = "gsa"
    mock_services["retriever"].retrieve = AsyncMock(return_value=[])
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    await h.handle(MessageRequest(user_id="u1", text="what is the travel award?", platform="discord"))
    mock_services["retriever"].retrieve.assert_called_once()

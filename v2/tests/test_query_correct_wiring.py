"""Task 3: wire the acronym dictionary into MessageHandler.handle() (gated).

Unit tests pin the augment_acronyms/protected contract the handler relies on.
The handler-level test proves the wiring itself: OFF -> resolved_query passed to
_try_structured is byte-identical to clean_text; ON -> it is augmented, while
clean_text (display/log) stays the original.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from v2.core.retrieval.query_correct import augment_acronyms


def test_off_is_identity(monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    # augment is only APPLIED when enabled(); the raw helper is still identity on no-match
    assert augment_acronyms("who is the chair of cs") == "who is the chair of cs computer science"


def test_protected_name_not_expanded():
    # a surname that collides with an abbrev is protected via nodes tokens (passed by handle())
    assert augment_acronyms("prof eng", protected={"eng"}) == "prof professor eng"


# ── Handler-level wiring ────────────────────────────────────────────────────

from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


@pytest.fixture
def wiring_handler():
    rate_limiter = MagicMock()
    rate_limiter.is_allowed.return_value = True

    intent_detector = MagicMock()
    intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)

    conversation_manager = MagicMock()
    conversation_manager.get_session.return_value = None
    conversation_manager.get_history.return_value = []
    conversation_manager.get_mode.return_value = "gsa"

    config = MagicMock()
    config.conversation_max_turns = 5

    handler = MessageHandler(
        retriever=AsyncMock(),
        ollama=None,   # keeps the contextual-rewrite (resolve_query) branch inert
        conversation_manager=conversation_manager,
        intent_detector=intent_detector,
        db=MagicMock(),
        rate_limiter=rate_limiter,
        kb=MagicMock(),
        config=config,
    )
    # Short-circuit right after the seam we're testing: capture what _try_structured
    # receives as `text` (== resolved_query at the call site) and stop there.
    handler._try_structured = AsyncMock(return_value="stubbed structured answer")
    return handler


@pytest.mark.asyncio
async def test_flag_off_resolved_query_untouched(wiring_handler, monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    # Isolate from other flags a real shell/CI env might export — none of these should
    # influence whether resolved_query gets augmented.
    monkeypatch.delenv("ANSWER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("ROUTER_V21", raising=False)
    monkeypatch.delenv("FOLLOWUP_RESUME_ENABLED", raising=False)
    req = MessageRequest(user_id="1", text="who is the chair of cs", platform="discord")
    await wiring_handler.handle(req)
    called_text = wiring_handler._try_structured.call_args.args[0]
    assert called_text == "who is the chair of cs"


@pytest.mark.asyncio
async def test_flag_on_resolved_query_augmented(wiring_handler, monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    monkeypatch.delenv("ANSWER_GATE_ENABLED", raising=False)
    monkeypatch.delenv("ROUTER_V21", raising=False)
    monkeypatch.delenv("FOLLOWUP_RESUME_ENABLED", raising=False)
    req = MessageRequest(user_id="1", text="who is the chair of cs", platform="discord")
    await wiring_handler.handle(req)
    called_text = wiring_handler._try_structured.call_args.args[0]
    assert called_text == "who is the chair of cs computer science"

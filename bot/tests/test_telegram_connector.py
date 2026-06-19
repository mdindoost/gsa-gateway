"""Tests for TelegramConnector command handlers."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageResponse
from bot.services.knowledge_base import Contact, Event, Resource


@pytest.fixture
def kb():
    kb = MagicMock()
    kb.events = [
        Event(
            name="GSA Mixer",
            date="2099-06-10",
            time="6:00 PM",
            location="Campus Center",
            description="Annual spring mixer.",
            organizer="GSA",
            rsvp_link="",
        )
    ]
    kb.contacts = {
        "president": Contact(
            role="GSA President",
            name="Fernando Vera",
            email="gsa-pres@njit.edu",
            office="Campus Center 110A",
        )
    }
    kb.resources = {
        "academic": [
            Resource(
                title="NJIT Library",
                description="Research databases.",
                url="https://library.njit.edu",
                category="academic",
            )
        ]
    }
    return kb


@pytest.fixture
def connector(kb):
    handler = MagicMock()
    handler.handle = AsyncMock(
        return_value=MessageResponse(text="Hello from GSA Gateway!")
    )
    return TelegramConnector(token="fake-token", handler=handler, kb=kb)


def _make_update_context(text="hello", args=None):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.message = message
    context = MagicMock()
    context.args = args or []
    return update, context


@pytest.mark.asyncio
async def test_on_message_calls_handler_and_replies(connector):
    update, context = _make_update_context("what is gsa?")
    await connector._on_message(update, context)
    connector.handler.handle.assert_called_once()
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "hello from gsa gateway" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_appends_source_note(connector):
    connector.handler.handle = AsyncMock(
        return_value=MessageResponse(text="GSA info.", source_note="GSA FAQ")
    )
    update, context = _make_update_context("tell me about gsa")
    await connector._on_message(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa faq" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_skips_empty_response(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=""))
    update, context = _make_update_context("  ")
    await connector._on_message(update, context)
    update.message.reply_text.assert_not_called()


# ── Unified mode dispatch (judging routes through the dispatcher) ──────────────

@pytest.fixture
def judging_setup():
    """A real JudgingSessionManager wired into a connector through a ModeDispatcher that
    shares the conversation ModeStore — the production wiring. Yields the shared store too."""
    import os
    import tempfile
    os.environ.setdefault("GSA_JUDGING_SCRYPT_N", "64")
    from bot.core.modes import ConversationModeStore, ModeDispatcher, ModeRegistry
    from v2.core.database.schema import create_all
    from v2.core.judging import db as jdb
    from v2.core.judging.session import JudgingSessionManager

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = create_all(db_path)
    eid = jdb.create_event(conn, "3MRP", criteria=["Q1"], top_n=1, score_min=1, score_max=5)
    jdb.set_event_status(conn, eid, "open")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    conn.close()

    handler = MagicMock()
    handler.handle = AsyncMock(return_value=MessageResponse(text="RAG answer", question_id=7))
    judging = JudgingSessionManager(db_path)
    store = ConversationModeStore()
    registry = ModeRegistry(store, judging=judging)
    dispatcher = ModeDispatcher(registry, judging=judging, conversation_handler=handler.handle)
    connector = TelegramConnector(
        token="fake", handler=handler, kb=MagicMock(),
        judging_manager=judging, dispatcher=dispatcher,
    )
    yield connector, handler, store
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_judging_trigger_routes_to_judging_not_handler(judging_setup):
    connector, handler, _store = judging_setup
    update, context = _make_update_context("judge mode")
    await connector._on_message(update, context)
    handler.handle.assert_not_called()                 # judging owned it
    reply_text = update.message.reply_text.call_args[0][0]
    assert "PIN" in reply_text


@pytest.mark.asyncio
async def test_idle_normal_message_routes_to_handler(judging_setup):
    connector, handler, _store = judging_setup
    update, context = _make_update_context("what is gsa?")
    await connector._on_message(update, context)
    handler.handle.assert_called_once()                # conversation owned it
    reply_text = update.message.reply_text.call_args[0][0]
    assert "rag answer" in reply_text.lower()


@pytest.mark.asyncio
async def test_judge_midflow_number_stays_in_judging(judging_setup):
    connector, handler, _store = judging_setup
    for text in ("judge mode", "J-001"):              # authenticate -> ready
        u, c = _make_update_context(text)
        await connector._on_message(u, c)
    handler.handle.reset_mock()
    # Now in JUDGE mode; a bare number is judging input, NOT a RAG question.
    u, c = _make_update_context("100")
    await connector._on_message(u, c)
    handler.handle.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_phrase_midjudging_owned_by_judging_not_store(judging_setup):
    # A judge mid-flow typing "free mode" must stay owned by judging (already in a judging
    # mode) and must NOT flip the shared conversation store to FREE behind their back.
    from bot.core.modes import Mode
    connector, handler, store = judging_setup
    for text in ("judge mode", "J-001"):              # -> ready (JUDGE)
        u, c = _make_update_context(text)
        await connector._on_message(u, c)
    handler.handle.reset_mock()
    u, c = _make_update_context("free mode")
    await connector._on_message(u, c)
    handler.handle.assert_not_called()                # judging owned it
    assert store.get("12345") == Mode.GSA             # conversation store untouched


# NOTE: tests for /events /contact /resources /help were removed — those v1 command
# handlers no longer exist on TelegramConnector (all-conversational migration; only
# /start + /qrcode remain). They were already failing on the base branch.

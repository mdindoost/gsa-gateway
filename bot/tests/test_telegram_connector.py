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


@pytest.mark.asyncio
async def test_cmd_events_lists_event_name(connector):
    update, context = _make_update_context("/events")
    await connector._cmd_events(update, context)
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa mixer" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_events_no_events(connector):
    connector.kb.events = []
    update, context = _make_update_context("/events")
    await connector._cmd_events(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "no upcoming events" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_no_args_lists_all(connector):
    update, context = _make_update_context("/contact")
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "fernando vera" in reply_text.lower()
    assert "gsa-pres@njit.edu" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_with_arg_filters(connector):
    update, context = _make_update_context("/contact president", args=["president"])
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa president" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_no_match(connector):
    update, context = _make_update_context("/contact zzz", args=["zzz"])
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "no matching" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_resources_no_args_lists_categories(connector):
    update, context = _make_update_context("/resources")
    await connector._cmd_resources(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "njit library" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_resources_with_category(connector):
    update, context = _make_update_context("/resources academic", args=["academic"])
    await connector._cmd_resources(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "njit library" in reply_text.lower()

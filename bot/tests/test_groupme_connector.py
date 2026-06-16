"""Tests for GroupMeConnector — polling, loop-safety, chunking, and the webhook seam."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.connectors.groupme_connector import (
    GroupMeConnector,
    _chunk,
    _strip_markdown,
)
from bot.core.message_handler import MessageResponse


def _make_connector(handler=None):
    handler = handler or MagicMock()
    if not isinstance(getattr(handler, "handle", None), AsyncMock):
        handler.handle = AsyncMock(return_value=MessageResponse(text="Hello!"))
    return GroupMeConnector(
        bot_id="bot123",
        access_token="tok",
        group_id="999",
        handler=handler,
        kb=MagicMock(),
        poll_interval=5,
    )


def _msg(mid, *, sender_type="user", text="hi", created_at=0, user_id="u1", name="Ada"):
    return {
        "id": str(mid),
        "sender_type": sender_type,
        "text": text,
        "created_at": created_at,
        "user_id": user_id,
        "name": name,
    }


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_strip_markdown_removes_bold():
    assert _strip_markdown("**Bold** and __also__ text") == "Bold and also text"


def test_chunk_short_text_single_piece():
    assert _chunk("short") == ["short"]


def test_chunk_empty_is_empty_list():
    assert _chunk("   ") == []


def test_chunk_splits_long_text_under_limit():
    text = "word " * 500  # ~2500 chars
    chunks = _chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


# ── Loop-safety / filtering ───────────────────────────────────────────────────

def test_should_process_user_with_text():
    assert GroupMeConnector._should_process(_msg(1)) is True


def test_should_process_skips_bot_messages():
    # critical: our own posts come back as sender_type='bot' — never reply to them
    assert GroupMeConnector._should_process(_msg(1, sender_type="bot")) is False


def test_should_process_skips_system_and_empty():
    assert GroupMeConnector._should_process(_msg(1, sender_type="system")) is False
    assert GroupMeConnector._should_process(_msg(1, text="   ")) is False


# ── Processing seam ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_message_calls_handler_and_posts_with_source():
    handler = MagicMock()
    handler.handle = AsyncMock(
        return_value=MessageResponse(text="**Answer**", source_note="Graduate Studies"))
    conn = _make_connector(handler)
    conn._post = AsyncMock()

    await conn._process_message(user_id="u1", text="a question")

    handler.handle.assert_awaited_once()
    req = handler.handle.await_args.args[0]
    assert req.platform == "groupme"
    posted = conn._post.await_args.args[0]
    assert "Answer" in posted and "**" not in posted  # markdown stripped
    assert "Source: Graduate Studies" in posted


@pytest.mark.asyncio
async def test_process_message_no_reply_when_blank():
    handler = MagicMock()
    handler.handle = AsyncMock(return_value=MessageResponse(text=""))
    conn = _make_connector(handler)
    conn._post = AsyncMock()
    await conn._process_message(user_id="u1", text="hi")
    conn._post.assert_not_awaited()


# ── Polling ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_once_processes_only_user_and_advances_cursor():
    conn = _make_connector()
    conn._post = AsyncMock()
    conn._last_id = "100"
    conn._fetch_messages = AsyncMock(return_value=[
        _msg(101, sender_type="user", created_at=101),
        _msg(102, sender_type="bot", created_at=102),  # our own echo — must be ignored
    ])

    await conn._poll_once()

    conn._fetch_messages.assert_awaited_once_with(after_id="100")
    # only the user message is handled, but cursor advances past the bot one too
    assert conn.handler.handle.await_count == 1
    assert conn._last_id == "102"


@pytest.mark.asyncio
async def test_poll_once_seeds_baseline_when_cursor_none():
    conn = _make_connector()
    conn._post = AsyncMock()
    conn._last_id = None
    conn._latest_message_id = AsyncMock(return_value="555")

    await conn._poll_once()

    assert conn._last_id == "555"
    conn.handler.handle.assert_not_called()  # baseline only — no backlog replay


# ── Webhook seam ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_callback_processes_user_payload():
    conn = _make_connector()
    conn._post = AsyncMock()
    await conn.handle_callback(_msg(1, sender_type="user", text="hello"))
    conn.handler.handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_callback_ignores_bot_payload():
    conn = _make_connector()
    conn._post = AsyncMock()
    await conn.handle_callback(_msg(1, sender_type="bot", text="echo"))
    conn.handler.handle.assert_not_called()


# ── Outbound payload contract ─────────────────────────────────────────────────

class _FakePost:
    """Minimal async context manager standing in for session.post(...)."""
    def __init__(self, recorder):
        self._recorder = recorder
        self.status = 202

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return ""


@pytest.mark.asyncio
async def test_post_sends_bot_id_and_text():
    conn = _make_connector()
    calls = []

    def fake_post(url, json=None):
        calls.append((url, json))
        return _FakePost(calls)

    conn._session = MagicMock()
    conn._session.post = fake_post

    await conn._post("hello group")

    assert len(calls) == 1
    url, payload = calls[0]
    assert url.endswith("/bots/post")
    assert payload == {"bot_id": "bot123", "text": "hello group"}

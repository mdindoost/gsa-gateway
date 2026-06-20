"""Telegram live-search offer plumbing: the web:{qid} button + _on_web_search handler.

The offer rides the existing _pending_feedback entry (which stores question_text), so a tap
re-issues the search through the handler's live_search seam. Ownership is hash-checked; a
missing/expired entry degrades politely; an empty result uses the shared 'found nothing' copy.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageResponse
from bot.core.live_query import LIVE_NOT_FOUND_MSG


@pytest.fixture
def connector():
    handler = MagicMock()
    handler.live_search = AsyncMock()
    handler.handle = AsyncMock(return_value=MessageResponse(text="hi"))
    return TelegramConnector(token="fake-token", handler=handler, kb=MagicMock())


def _message_update(text="what are the library hours", uid=12345):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.message = message
    update.effective_user = SimpleNamespace(id=uid)
    return update, message


def _callback(connector, data, uid=12345):
    query = MagicMock()
    query.data = data
    query.from_user = SimpleNamespace(id=uid)
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


# ── keyboard ──────────────────────────────────────────────────────────────────
def _all_callback_data(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_keyboard_includes_web_button_when_offered(connector):
    kb = connector._build_feedback_keyboard(42, offer_live_search=True)
    assert "web:42" in _all_callback_data(kb)


def test_keyboard_omits_web_button_by_default(connector):
    kb = connector._build_feedback_keyboard(42)
    assert "web:42" not in _all_callback_data(kb)
    assert "fb:42:up" in _all_callback_data(kb)


# ── _on_web_search ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_web_search_happy_path(connector):
    connector._register_pending(7, 12345, "library hours", "some deflection answer")
    connector.handler.live_search.return_value = SimpleNamespace(
        text="From NJIT's website: open 24h.", source_url="https://library.njit.edu"
    )
    update, query = _callback(connector, "web:7")
    await connector._on_web_search(update, None)
    connector.handler.live_search.assert_awaited_once_with("library hours")
    sent = " ".join(str(c.args[0]) for c in query.message.reply_text.call_args_list)
    assert "open 24h" in sent


@pytest.mark.asyncio
async def test_web_search_empty_uses_shared_message(connector):
    connector._register_pending(7, 12345, "unicorn rentals", "ans")
    connector.handler.live_search.return_value = None
    update, query = _callback(connector, "web:7")
    await connector._on_web_search(update, None)
    sent = " ".join(str(c.args[0]) for c in query.message.reply_text.call_args_list)
    assert LIVE_NOT_FOUND_MSG in sent


@pytest.mark.asyncio
async def test_web_search_missing_pending_degrades(connector):
    update, query = _callback(connector, "web:999")  # never registered
    await connector._on_web_search(update, None)
    connector.handler.live_search.assert_not_called()


@pytest.mark.asyncio
async def test_web_search_ownership_mismatch_blocks(connector):
    connector._register_pending(7, 12345, "library hours", "ans")
    update, query = _callback(connector, "web:7", uid=99999)  # different user
    await connector._on_web_search(update, None)
    connector.handler.live_search.assert_not_called()


# ── _on_message attaches the offer + registers pending ────────────────────────
@pytest.mark.asyncio
async def test_on_message_attaches_web_offer_on_deflection(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(
        text="For current hours, see library.njit.edu.", question_id=5, offer_live_search=True,
    ))
    update, message = _message_update()
    await connector._on_message(update, None)
    markup = message.reply_text.call_args.kwargs["reply_markup"]
    assert "web:5" in _all_callback_data(markup)
    # pending registered with the question text so the offer tap can re-issue it
    assert connector._pending_feedback[5]["question_text"] == "what are the library hours"


@pytest.mark.asyncio
async def test_on_message_no_offer_when_flag_false(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(
        text="Open 8AM-midnight.", question_id=5, offer_live_search=False,
    ))
    update, message = _message_update()
    await connector._on_message(update, None)
    markup = message.reply_text.call_args.kwargs["reply_markup"]
    assert "web:5" not in _all_callback_data(markup)


# ── 🔄 same-answer dead-end offers a live search ──────────────────────────────
@pytest.mark.asyncio
async def test_retry_dead_end_offers_web_search(connector, monkeypatch):
    import bot.config as botcfg
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "k")
    connector.handler.db = None
    connector._register_pending(7, 12345, "library hours", "the same answer")
    # retry returns an identical answer → similarity high → dead end
    connector.handler.retry_question = AsyncMock(
        return_value=MessageResponse(text="the same answer", question_id=8)
    )
    update, query = _callback(connector, "fb:7:retry")
    await connector._on_feedback(update, None)
    # a dead-end reply carries the web offer, re-registered under the original qid
    markups = [c.kwargs.get("reply_markup") for c in query.message.reply_text.call_args_list]
    web_offered = any(m and "web:7" in _all_callback_data(m) for m in markups)
    assert web_offered
    assert 7 in connector._pending_feedback


@pytest.mark.asyncio
async def test_retry_dead_end_no_offer_when_feature_off(connector, monkeypatch):
    import bot.config as botcfg
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "")
    connector.handler.db = None
    connector._register_pending(7, 12345, "library hours", "the same answer")
    connector.handler.retry_question = AsyncMock(
        return_value=MessageResponse(text="the same answer", question_id=8)
    )
    update, query = _callback(connector, "fb:7:retry")
    await connector._on_feedback(update, None)
    markups = [c.kwargs.get("reply_markup") for c in query.message.reply_text.call_args_list]
    web_offered = any(m and "web:7" in _all_callback_data(m) for m in markups if m)
    assert not web_offered


# ── pending lifetime: 👎 must NOT pop early (the offer needs question_text later) ──
@pytest.mark.asyncio
async def test_thumbs_down_keeps_pending_for_later_offer(connector):
    connector.handler.db = None
    connector._register_pending(7, 12345, "library hours", "ans")
    update, query = _callback(connector, "fb:7:down")
    await connector._on_feedback(update, None)
    assert 7 in connector._pending_feedback   # survives for the detail-step web offer

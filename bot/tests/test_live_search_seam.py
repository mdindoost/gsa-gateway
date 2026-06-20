"""MessageHandler.live_search — the single seam wrapping maybe_answer_live.

One place constructs the provider wiring (brave_search / http_fetch / the generate lambda)
and the feature-gate, so the auto-fire path and the connector offer-tap path can't drift, and
a stale on-screen button tapped after the key was pulled degrades to None (never a crash).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.config as botcfg
import bot.core.message_handler as mh
from bot.core.message_handler import MessageHandler


def _handler(ollama=None):
    return MessageHandler(
        retriever=AsyncMock(), ollama=ollama, conversation_manager=MagicMock(),
        intent_detector=MagicMock(), db=MagicMock(), rate_limiter=MagicMock(),
        kb=MagicMock(), config=MagicMock(),
    )


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "test-key")


@pytest.mark.asyncio
async def test_live_search_returns_live_answer_when_on(live_on, monkeypatch):
    answer = SimpleNamespace(text="From NJIT's website: open 24h.", source_url="https://x.njit.edu")
    spy = AsyncMock(return_value=answer)
    monkeypatch.setattr(mh, "maybe_answer_live", spy)
    handler = _handler(ollama=AsyncMock())
    result = await handler.live_search("library hours")
    assert result is answer
    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_live_search_none_when_disabled(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "test-key")
    spy = AsyncMock()
    monkeypatch.setattr(mh, "maybe_answer_live", spy)
    handler = _handler(ollama=AsyncMock())
    assert await handler.live_search("library hours") is None
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_search_none_without_key(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "")
    spy = AsyncMock()
    monkeypatch.setattr(mh, "maybe_answer_live", spy)
    handler = _handler(ollama=AsyncMock())
    assert await handler.live_search("library hours") is None
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_search_none_without_ollama(live_on, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(mh, "maybe_answer_live", spy)
    handler = _handler(ollama=None)
    assert await handler.live_search("library hours") is None
    spy.assert_not_awaited()

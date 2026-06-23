"""Phase 2: Telegram unsend. broadcaster.delete → adapter → connector override, with
telegram.error classified at the connector into terminal (Forbidden / >48h expiry) vs
transient (RetryAfter/net) vs success (not-found), surfaced via DeliveryResult."""
from __future__ import annotations
import sys
import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from telegram.error import BadRequest, Forbidden, RetryAfter
from v2.core.connectors.telegram_connector import TelegramConnector


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


# ── broadcaster.delete ──────────────────────────────────────────────────────────
def test_broadcaster_delete_returns_true_and_propagates():
    import pytest
    from unittest.mock import AsyncMock, MagicMock
    from bot.services.telegram_broadcaster import TelegramBroadcaster
    b = TelegramBroadcaster.__new__(TelegramBroadcaster)
    b._bot = MagicMock()
    b._bot.delete_message = AsyncMock(return_value=True)
    assert _run(b.delete("-100123", "55")) is True
    b._bot.delete_message.assert_awaited_once()
    # failures PROPAGATE (the connector classifies them), they are not swallowed to a bool
    b._bot.delete_message = AsyncMock(side_effect=BadRequest("Message can't be deleted"))
    with pytest.raises(BadRequest):
        _run(b.delete("-100123", "55"))


# ── connector override classifies the telegram.error ───────────────────────────
class _Adapter:
    """Fake adapter whose delete_message raises a given telegram.error (or returns)."""
    def __init__(self, raise_exc=None):
        self._raise = raise_exc

    async def delete_message(self, channel, message_id):
        if self._raise:
            raise self._raise


def _delete(raise_exc=None):
    return _run(TelegramConnector(client=_Adapter(raise_exc)).delete_message("-100", "55"))


def test_telegram_delete_success():
    r = _delete(None)
    assert r.success is True and r.platform == "telegram"


def test_telegram_not_found_is_success():
    r = _delete(BadRequest("Message to delete not found"))
    assert r.success is True            # already gone = goal achieved


def test_telegram_expiry_is_terminal():
    r = _delete(BadRequest("Message can't be deleted"))
    assert r.success is False and r.terminal is True


def test_telegram_forbidden_is_terminal():
    r = _delete(Forbidden("not enough rights to delete a message"))
    assert r.success is False and r.terminal is True


def test_telegram_retryafter_is_transient():
    r = _delete(RetryAfter(5))
    assert r.success is False and r.terminal is False

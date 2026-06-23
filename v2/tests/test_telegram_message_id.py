"""Phase 0 of the scheduled-deletion feature: every Telegram send must persist the REAL
platform message_id + chat_id (not the old "telegram-broadcast" sentinel), so a later
deletion can unsend the exact message. Covers the broadcaster → adapter → connector path."""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.connectors.telegram_connector import TelegramConnector
from v2.integration.telegram_client import TelegramClientAdapter


def _run(coro):
    # Match the repo's test style (get_event_loop().run_until_complete) — does NOT close the
    # loop, so it never pollutes other tests' legacy get_event_loop() usage in the same process.
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeChat:
    def __init__(self, cid):
        self.id = cid


class _FakeMessage:
    """Mimics telegram.Message — what bot.send_message() returns."""
    def __init__(self, mid, cid):
        self.message_id = mid
        self.chat = _FakeChat(cid)


class _FakeBroadcaster:
    def __init__(self, message):
        self._message = message          # the Message to return (or None = failure)

    async def broadcast(self, content, parse_mode="HTML"):
        return self._message


# ── adapter: surfaces the real (message_id, chat_id) ────────────────────────────
def test_adapter_returns_real_message_id_and_chat():
    adapter = TelegramClientAdapter(_FakeBroadcaster(_FakeMessage(42, -1001234567890)))
    out = _run(adapter.send_message("ignored-channel", "hello"))
    assert out == (42, -1001234567890)         # NOT the "telegram-broadcast" sentinel


def test_adapter_raises_on_failed_broadcast():
    adapter = TelegramClientAdapter(_FakeBroadcaster(None))   # broadcast returned None
    try:
        _run(adapter.send_message("c", "hi"))
        assert False, "expected RuntimeError on failed broadcast"
    except RuntimeError:
        pass


# ── connector: stores the real id in message_id and the real chat in channel ────
class _FakeClient:
    def __init__(self, ret):
        self._ret = ret

    async def send_message(self, channel, content, parse_mode="HTML", **kw):
        return self._ret


def test_connector_delivery_carries_real_id_and_chat():
    conn = TelegramConnector(client=_FakeClient((42, -1001234567890)))
    r = _run(conn.send_text("hi", "org-setting-channel"))
    assert r.success
    assert r.message_id == "42"                  # real, deletable id
    assert r.channel == "-1001234567890"         # the EXACT chat the message went to


def test_connector_media_path_also_captures_real_id():
    # send_media funnels through the same _send → adapter tuple, so it captures the id too
    conn = TelegramConnector(client=_FakeClient((7, -5)))
    r = _run(conn.send_media("x", "/tmp/p.png", "c"))
    assert r.success and r.message_id == "7" and r.channel == "-5"


def test_registry_persists_real_telegram_id_and_chat():
    # Phase 0's whole point is persistence: the REAL id+chat must land in post_deliveries.
    from v2.core.database.schema import create_all
    from v2.core.connectors.registry import ConnectorRegistry
    from v2.core.connectors.base import Post
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.execute("INSERT INTO posts(id,org_id,type,content,channels,status) "
                 "VALUES(1,1,'broadcast','hi','[\"telegram\"]','sending')")
    conn.commit()
    reg = ConnectorRegistry(conn=conn)
    reg.register(TelegramConnector(client=_FakeClient((42, -1001234567890))))
    post = Post(id=1, content="hi", channels=["telegram"],
                platform_channels={"telegram": "setting-value"})
    _run(reg.publish(post))
    row = conn.execute(
        "SELECT platform, message_id, channel FROM post_deliveries WHERE post_id=1").fetchone()
    assert row["platform"] == "telegram"
    assert row["message_id"] == "42"
    assert row["channel"] == "-1001234567890"     # the real chat, not the setting value
    conn.close()

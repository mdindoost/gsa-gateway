import sys
import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.connectors.registry import ConnectorRegistry
from v2.core.connectors.discord_connector import DiscordConnector


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


class _FakeClient:
    async def delete_message(self, channel, message_id):
        pass

    async def ping(self):
        return True


def test_registry_routes_delete_to_connector():
    reg = ConnectorRegistry()
    reg.register(DiscordConnector(client=_FakeClient()))
    r = _run(reg.delete_delivery("discord", "chan", "42"))
    assert r.success is True and r.platform == "discord"


def test_registry_delete_unknown_platform():
    r = _run(ConnectorRegistry().delete_delivery("nope", "c", "1"))
    assert r.success is False and "no connector" in (r.error or "").lower()

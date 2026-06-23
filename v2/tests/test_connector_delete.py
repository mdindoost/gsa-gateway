import sys
import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.connectors.groupme_connector import GroupMeConnector
from v2.core.connectors.discord_connector import DiscordConnector


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


# ── default (send-only platforms inherit unsupported) ───────────────────────────
def test_default_delete_is_unsupported():
    conn = GroupMeConnector(client=None)
    r = _run(conn.delete_message("grp", "123"))
    assert r.success is False
    assert "unsupported" in (r.error or "").lower()
    assert r.platform == "groupme"


# ── Discord override ────────────────────────────────────────────────────────────
class _FakeDiscordClient:
    def __init__(self, raise_exc=None):
        self.calls = []
        self._raise = raise_exc

    async def delete_message(self, channel, message_id):
        self.calls.append((channel, message_id))
        if self._raise:
            raise self._raise

    async def ping(self):
        return True


def test_discord_delete_success():
    client = _FakeDiscordClient()
    r = _run(DiscordConnector(client=client).delete_message("gsa-announcements", "999"))
    assert r.success is True and r.platform == "discord"
    assert client.calls == [("gsa-announcements", "999")]


def test_discord_delete_not_found_is_success():
    # adapter returns normally on NotFound (message already gone = goal achieved)
    client = _FakeDiscordClient(raise_exc=None)
    r = _run(DiscordConnector(client=client).delete_message("c", "1"))
    assert r.success is True


def test_discord_delete_hard_error_is_failure():
    client = _FakeDiscordClient(raise_exc=RuntimeError("boom"))
    r = _run(DiscordConnector(client=client).delete_message("c", "1"))
    assert r.success is False and "boom" in (r.error or "")

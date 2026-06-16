"""Tests for the connector pattern (Step 5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.connectors.base import Button, DeliveryResult, Post
from v2.core.connectors.discord_connector import DiscordConnector
from v2.core.connectors.groupme_connector import GroupMeConnector
from v2.core.connectors.registry import ConnectorRegistry
from v2.core.connectors.stub_connector import StubConnector
from v2.core.connectors.telegram_connector import TelegramConnector, markdown_to_html
from v2.core.database.schema import create_all


# ── stub ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_records_calls():
    stub = StubConnector()
    r = await stub.send_text("hello", "general")
    assert r.success and r.platform == "stub" and r.channel == "general"
    assert stub.calls[0]["kind"] == "text" and stub.calls[0]["content"] == "hello"


# ── registry publish ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_fans_out_to_all_targets():
    reg = ConnectorRegistry()
    reg.register(StubConnector("discord"))
    reg.register(StubConnector("telegram"))
    post = Post(content="hi", channels=["discord", "telegram"],
                platform_channels={"discord": "gsa-announcements", "telegram": "@chan"})
    results = await reg.publish(post)
    assert len(results) == 2
    assert all(r.success for r in results)
    assert {r.platform for r in results} == {"discord", "telegram"}
    # each connector got the right channel
    assert reg.get("discord").calls[0]["channel"] == "gsa-announcements"
    assert reg.get("telegram").calls[0]["channel"] == "@chan"


@pytest.mark.asyncio
async def test_target_platforms_override():
    reg = ConnectorRegistry()
    reg.register(StubConnector("discord"))
    reg.register(StubConnector("telegram"))
    post = Post(content="hi", channels=["discord", "telegram"])
    results = await reg.publish(post, target_platforms=["telegram"])
    assert [r.platform for r in results] == ["telegram"]
    assert reg.get("discord").calls == []  # discord skipped


@pytest.mark.asyncio
async def test_disabled_connector_skipped():
    reg = ConnectorRegistry()
    reg.register(StubConnector("discord", enabled=False))
    reg.register(StubConnector("telegram"))
    post = Post(content="hi", channels=["discord", "telegram"])
    results = await reg.publish(post)
    assert [r.platform for r in results] == ["telegram"]


@pytest.mark.asyncio
async def test_partial_failure_does_not_sink_batch():
    class Raiser(StubConnector):
        async def send_text(self, content, channel, metadata=None):
            raise RuntimeError("boom")

    reg = ConnectorRegistry()
    reg.register(Raiser("discord"))
    reg.register(StubConnector("telegram"))
    post = Post(content="hi", channels=["discord", "telegram"])
    results = await reg.publish(post)  # must not raise
    by = {r.platform: r for r in results}
    assert by["discord"].success is False and "boom" in by["discord"].error
    assert by["telegram"].success is True


@pytest.mark.asyncio
async def test_stub_fail_flag_returns_failed_result():
    reg = ConnectorRegistry()
    reg.register(StubConnector("discord", fail=True))
    results = await reg.publish(Post(content="x", channels=["discord"]))
    assert results[0].success is False and results[0].error == "stub failure"


@pytest.mark.asyncio
async def test_media_and_interactive_routing():
    stub = StubConnector("discord")
    reg = ConnectorRegistry()
    reg.register(stub)
    await reg.publish(Post(content="m", channels=["discord"], media_path="/tmp/x.png"))
    await reg.publish(Post(content="b", channels=["discord"],
                           buttons=[Button("Yes", "cb_yes")]))
    kinds = [c["kind"] for c in stub.calls]
    assert kinds == ["media", "interactive"]


@pytest.mark.asyncio
async def test_health_check_all():
    reg = ConnectorRegistry()
    reg.register(StubConnector("discord", healthy=True))
    reg.register(StubConnector("telegram", healthy=False))
    assert await reg.health_check_all() == {"discord": True, "telegram": False}


# ── post_deliveries audit logging ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_logs_to_post_deliveries():
    conn = create_all(":memory:")
    org = conn.execute(
        "INSERT INTO organizations(name,slug,type) VALUES('GSA','gsa','gsa')"
    ).lastrowid
    post_id = conn.execute(
        "INSERT INTO posts(org_id,type,content,channels) VALUES(?,?,?,?)",
        (org, "broadcast", "hello", '["discord","telegram"]'),
    ).lastrowid
    conn.commit()

    reg = ConnectorRegistry(conn=conn)
    reg.register(StubConnector("discord"))
    reg.register(StubConnector("telegram", fail=True))
    post = Post(id=post_id, content="hello", channels=["discord", "telegram"],
                platform_channels={"discord": "gsa-announcements", "telegram": "@chan"})
    await reg.publish(post)

    rows = conn.execute(
        "SELECT platform, channel, status, error, message_id FROM post_deliveries "
        "WHERE post_id=? ORDER BY platform", (post_id,)
    ).fetchall()
    assert len(rows) == 2
    d = {r["platform"]: r for r in rows}
    assert d["discord"]["status"] == "success" and d["discord"]["message_id"]
    assert d["telegram"]["status"] == "failed" and d["telegram"]["error"] == "stub failure"
    conn.close()


# ── platform formatting ──────────────────────────────────────────────────────

def test_discord_format_passes_markdown_and_appends_signature():
    c = DiscordConnector()
    out = c.format_content("**Bold** and *italic*", "_GSA_")
    assert out == "**Bold** and *italic*\n\n_GSA_"  # markdown untouched


def test_telegram_format_converts_to_html():
    c = TelegramConnector()
    out = c.format_content("**Bold** and *italic*", "_NJIT GSA_")
    assert "<b>Bold</b>" in out
    assert "<i>italic</i>" in out
    assert "<i>NJIT GSA</i>" in out


def test_telegram_html_escapes_then_formats():
    # Raw angle brackets must be escaped, not interpreted as tags.
    assert markdown_to_html("a < b & c") == "a &lt; b &amp; c"
    assert markdown_to_html("**x<y**") == "<b>x&lt;y</b>"


def test_unwired_connectors_fail_cleanly():
    import asyncio
    for conn in (DiscordConnector(), TelegramConnector(), GroupMeConnector()):
        r = asyncio.run(conn.send_text("hi", "chan"))
        assert isinstance(r, DeliveryResult) and r.success is False
        assert "not wired" in r.error


def test_groupme_format_strips_markdown():
    c = GroupMeConnector()
    out = c.format_content("**Bold** line", "_NJIT GSA_")
    assert "**" not in out and out.startswith("Bold line")


@pytest.mark.asyncio
async def test_groupme_publish_via_registry():
    class FakeGM:
        async def send_message(self, channel, content, **kw):
            assert channel == "GSAGateWayNJIT"
            assert content == "Hello GroupMe"
            return "gm-1"

        async def ping(self):
            return True

    reg = ConnectorRegistry()
    reg.register(GroupMeConnector(client=FakeGM()))
    post = Post(content="**Hello** GroupMe", channels=["groupme"],
                platform_channels={"groupme": "GSAGateWayNJIT"})
    results = await reg.publish(post)
    assert len(results) == 1 and results[0].success and results[0].platform == "groupme"

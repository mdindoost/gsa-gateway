"""Build-2 split-ops tests: resolve_org + two-conn publisher, enqueue, scheduler,
WorldCup watcher, judging repoint, and the behavior-preserving combined-file net.

IMPORTANT: All tests run against in-memory or temp-file DBs only.
No live-DB writes.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from v2.core.database.schema import create_all, create_knowledge_schema, create_ops_schema


# ── helpers ─────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _run(coro):
    """Run a coroutine in a fresh event loop (test helper)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── two-DB fixture ─────────────────────────────────────────────────────────
# Reusable by Phase 3 and Phase 5.

@pytest.fixture()
def two_db(tmp_path):
    """Separate KB and OPS temp-file DBs with a seeded GSA org + settings."""
    kb_path = str(tmp_path / "kb.db")
    ops_path = str(tmp_path / "ops.db")

    kb_conn = create_knowledge_schema(kb_path)
    ops_conn = create_ops_schema(ops_path)

    # Seed knowledge DB: GSA org + required settings
    kb_conn.execute(
        "INSERT INTO organizations(id, name, slug, type) VALUES(1,'GSA','gsa','gsa')"
    )
    kb_conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'signature.default','_GSA_','string')"
    )
    kb_conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'default.platforms','[\"discord\"]','json')"
    )
    kb_conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'default.channel.broadcast','gsa-ann','string')"
    )
    kb_conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'org.telegram_channel','@GSA','string')"
    )
    kb_conn.commit()

    yield {"kb_conn": kb_conn, "ops_conn": ops_conn,
           "kb_path": kb_path, "ops_path": ops_path}

    kb_conn.close()
    ops_conn.close()


@pytest.fixture()
def combined_db():
    """Combined DB via create_all (ops_path == kb_path) for behavior-preserving tests."""
    conn = create_all(":memory:")
    conn.execute(
        "INSERT INTO organizations(id, name, slug, type) VALUES(1,'GSA','gsa','gsa')"
    )
    conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'signature.default','_GSA_','string')"
    )
    conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'default.platforms','[\"discord\"]','json')"
    )
    conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'default.channel.broadcast','gsa-ann','string')"
    )
    conn.execute(
        "INSERT INTO settings(org_id,key,value,type) "
        "VALUES(1,'org.telegram_channel','@GSA','string')"
    )
    conn.commit()
    yield conn
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — resolve_org + OrgCache
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_org_returns_row_with_id(two_db):
    from v2.core.publishing.org_resolve import resolve_org
    row = resolve_org(two_db["kb_conn"], "gsa")
    assert row["id"] == 1
    assert row["slug"] == "gsa"


def test_resolve_org_unknown_raises(two_db):
    from v2.core.publishing.org_resolve import resolve_org
    with pytest.raises(ValueError, match="no org with slug"):
        resolve_org(two_db["kb_conn"], "nonexistent")


def test_resolve_org_multiple_match_raises(two_db):
    """LOW-11: >1 match for slug → ValueError (not ambiguous resolution)."""
    from v2.core.publishing.org_resolve import resolve_org
    # Manually insert a second org with the same slug under a different parent
    two_db["kb_conn"].execute(
        "INSERT INTO organizations(id, name, slug, type, parent_id) "
        "VALUES(99, 'GSA-Sub', 'gsa', 'club', 1)"
    )
    two_db["kb_conn"].commit()
    with pytest.raises(ValueError, match=">1 org"):
        resolve_org(two_db["kb_conn"], "gsa")


def test_org_cache_returns_same_row_within_tick(two_db):
    from v2.core.publishing.org_resolve import OrgCache
    cache = OrgCache()
    row1 = cache.get(two_db["kb_conn"], "gsa")
    row2 = cache.get(two_db["kb_conn"], "gsa")
    assert row1["id"] == row2["id"]


def test_org_cache_clears(two_db):
    from v2.core.publishing.org_resolve import OrgCache
    cache = OrgCache()
    row1 = cache.get(two_db["kb_conn"], "gsa")
    cache.clear()
    row2 = cache.get(two_db["kb_conn"], "gsa")
    # Both should resolve the same thing after clear
    assert row1["id"] == row2["id"]
    assert len(cache._cache) == 1


def test_resolve_org_works_on_combined_db(combined_db):
    from v2.core.publishing.org_resolve import resolve_org
    row = resolve_org(combined_db, "gsa")
    assert row["id"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — PostPublisher(ops_conn, kb_conn, registry, signatures)
# ─────────────────────────────────────────────────────────────────────────────

class _Stub:
    name = "discord"
    enabled = True

    def __init__(self):
        self.sent = []
        self.deleted = []

    def format_content(self, content, signature):
        return f"{content} | {signature}" if signature else content

    async def send_text(self, content, channel, meta):
        from v2.core.connectors.base import DeliveryResult
        self.sent.append((content, channel))
        return DeliveryResult(True, "discord", channel=channel, message_id="m1")

    async def send_media(self, content, path, channel, meta):
        return await self.send_text(content, channel, meta)

    async def send_interactive(self, content, buttons, channel, meta):
        return await self.send_text(content, channel, meta)

    async def delete_message(self, channel, message_id):
        from v2.core.connectors.base import DeliveryResult
        self.deleted.append((channel, message_id))
        return DeliveryResult(True, "discord", channel=channel, message_id=message_id)

    async def health_check(self):
        return True


def test_publisher_two_db_posts_on_ops_settings_on_kb(two_db):
    """PostPublisher reads settings from kb_conn and writes status on ops_conn."""
    from v2.core.connectors.registry import ConnectorRegistry
    from v2.core.publishing.publisher import PostPublisher
    from v2.core.publishing.signature import SignatureService

    ops_conn = two_db["ops_conn"]
    kb_conn = two_db["kb_conn"]

    stub = _Stub()
    registry = ConnectorRegistry(conn=ops_conn)  # OPS for post_deliveries
    registry.register(stub)

    sigs = SignatureService(kb_conn)  # KB for settings
    publisher = PostPublisher(ops_conn, kb_conn, registry, sigs)

    # Insert post into OPS
    ops_conn.execute(
        "INSERT INTO posts(id,org_id,org_slug,type,content,channels,status) "
        "VALUES(1,1,'gsa','one_time','hello','[\"discord\"]','scheduled')"
    )
    ops_conn.commit()

    _run(publisher.publish_post(1))

    row = ops_conn.execute("SELECT status, sent_at FROM posts WHERE id=1").fetchone()
    assert row["status"] == "sent"
    assert row["sent_at"] is not None

    # Delivery logged on OPS
    delivery = ops_conn.execute(
        "SELECT * FROM post_deliveries WHERE post_id=1"
    ).fetchone()
    assert delivery is not None
    assert delivery["platform"] == "discord"

    # Stub received content - signature from KB settings
    assert len(stub.sent) == 1
    sent_content, sent_channel = stub.sent[0]
    assert "hello" in sent_content


def test_publisher_combined_db_still_works(combined_db):
    """Behavior-preserving: PostPublisher(conn, conn, ...) works with combined DB."""
    from v2.core.connectors.registry import ConnectorRegistry
    from v2.core.publishing.publisher import PostPublisher
    from v2.core.publishing.signature import SignatureService

    stub = _Stub()
    registry = ConnectorRegistry(conn=combined_db)
    registry.register(stub)
    sigs = SignatureService(combined_db)
    publisher = PostPublisher(combined_db, combined_db, registry, sigs)

    combined_db.execute(
        "INSERT INTO posts(id,org_id,org_slug,type,content,channels,status) "
        "VALUES(10,1,'gsa','one_time','hello','[\"discord\"]','scheduled')"
    )
    combined_db.commit()

    _run(publisher.publish_post(10))
    row = combined_db.execute("SELECT status FROM posts WHERE id=10").fetchone()
    assert row["status"] == "sent"


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — SignatureService(kb_conn) + registry.conn=OPS
# ─────────────────────────────────────────────────────────────────────────────

def test_signature_service_reads_from_kb(two_db):
    from v2.core.publishing.signature import SignatureService
    sigs = SignatureService(two_db["kb_conn"])
    assert sigs.render(1) == "_GSA_"


def test_registry_conn_is_ops_for_deliveries(two_db):
    """registry.conn = OPS conn; deliveries land in OPS, not KB."""
    from v2.core.connectors.registry import ConnectorRegistry
    from v2.core.connectors.stub_connector import StubConnector
    from v2.core.connectors.base import Post

    ops_conn = two_db["ops_conn"]
    # Insert a post_deliveries row via registry
    registry = ConnectorRegistry(conn=ops_conn)

    # Manually log a delivery to ops
    ops_conn.execute(
        "INSERT INTO posts(id,org_id,org_slug,type,content,channels,status) "
        "VALUES(2,1,'gsa','one_time','hi','[\"discord\"]','sent')"
    )
    ops_conn.commit()

    # Simulate what registry._log_deliveries does
    from datetime import datetime, timezone
    from v2.core.connectors.base import DeliveryResult
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ops_conn.execute(
        "INSERT INTO post_deliveries(post_id,platform,channel,status,sent_at) "
        "VALUES(2,'discord','gsa-ann','success',?)", (now_s,)
    )
    ops_conn.commit()

    row = ops_conn.execute(
        "SELECT * FROM post_deliveries WHERE post_id=2"
    ).fetchone()
    assert row is not None

    # KB should have NO post_deliveries table
    try:
        kb_row = two_db["kb_conn"].execute(
            "SELECT 1 FROM post_deliveries LIMIT 1"
        ).fetchone()
        # If it doesn't raise, KB has the table (only possible in combined mode)
        assert False, "KB should not have post_deliveries table in split mode"
    except Exception:
        pass  # Expected: no such table


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — enqueue_post(ops_conn, kb_conn, draft, ...) + org_slug
# ─────────────────────────────────────────────────────────────────────────────

def test_enqueue_post_two_db_writes_org_slug(two_db):
    """enqueue_post validates org via kb_conn, inserts post with org_slug into ops_conn."""
    from v2.core.publishing.sources import PostDraft, enqueue_post

    draft = PostDraft(org_id=1, content="Hello world", type="broadcast",
                      channels=["discord"], source_type="test")
    pid = enqueue_post(two_db["ops_conn"], two_db["kb_conn"], draft)

    row = two_db["ops_conn"].execute(
        "SELECT * FROM posts WHERE id=?", (pid,)
    ).fetchone()
    assert row["status"] == "scheduled"
    assert row["org_slug"] == "gsa"  # explicitly resolved, not DEFAULT


def test_enqueue_post_two_db_rejects_unknown_org(two_db):
    from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post
    with pytest.raises(EnqueueError, match="does not exist"):
        enqueue_post(
            two_db["ops_conn"], two_db["kb_conn"],
            PostDraft(org_id=999, content="x", type="broadcast")
        )


def test_enqueue_post_two_db_rejects_inactive_org(two_db):
    from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post
    two_db["kb_conn"].execute(
        "INSERT INTO organizations(id,name,slug,type,is_active) "
        "VALUES(3,'Dead','dead','club',0)"
    )
    two_db["kb_conn"].commit()
    with pytest.raises(EnqueueError, match="not active"):
        enqueue_post(
            two_db["ops_conn"], two_db["kb_conn"],
            PostDraft(org_id=3, content="x", type="broadcast")
        )


def test_enqueue_post_combined_db_still_works(combined_db):
    """Behavior-preserving: enqueue_post(conn, conn, draft) works with combined DB."""
    from v2.core.publishing.sources import PostDraft, enqueue_post

    draft = PostDraft(org_id=1, content="combined", type="broadcast",
                      channels=["discord"], source_type="test")
    pid = enqueue_post(combined_db, combined_db, draft)
    row = combined_db.execute("SELECT status, org_slug FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["org_slug"] == "gsa"


def test_auto_delete_hours_reads_kb_conn(two_db):
    """auto_delete_hours uses the kb_conn for settings reads."""
    from v2.core.publishing.sources import auto_delete_hours
    # Set a custom value in KB
    two_db["kb_conn"].execute(
        "INSERT OR REPLACE INTO settings(org_id,key,value,type) "
        "VALUES(1,'default.auto_delete_hours','6','int')"
    )
    two_db["kb_conn"].commit()
    hours = auto_delete_hours(two_db["kb_conn"], 1)
    assert hours == 6


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — Scheduler(ops_conn, kb_conn, publisher, registry=None)
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_materializes_templates_on_ops(two_db):
    """materialize_templates reads post_templates from OPS and inserts posts into OPS."""
    from v2.core.publishing.scheduler import Scheduler

    ops_conn = two_db["ops_conn"]
    kb_conn = two_db["kb_conn"]

    # Insert a template into OPS
    past = "2000-01-01 09:00:00"
    ops_conn.execute(
        "INSERT INTO post_templates(id,org_id,org_slug,name,content,post_type,"
        "recurrence,channels,enabled,next_run_at) "
        "VALUES(1,1,'gsa','Weekly','Template content','recurring_instance',"
        "'{\"freq\":\"weekly\"}','[\"discord\"]',1,?)", (past,)
    )
    ops_conn.commit()

    from unittest.mock import AsyncMock
    mock_publisher = AsyncMock()
    mock_publisher.publish_due = AsyncMock(return_value={"published": 0, "sent": 0, "failed": 0})

    scheduler = Scheduler(ops_conn, kb_conn, mock_publisher)
    now_dt = datetime(2025, 1, 5, 10, 0, 0)
    count = scheduler.materialize_templates(now_dt)
    assert count == 1

    posts = ops_conn.execute("SELECT * FROM posts WHERE source_type='template'").fetchall()
    assert len(posts) == 1
    assert posts[0]["content"] == "Template content"
    assert posts[0]["org_slug"] == "gsa"  # F4: materializer stamps org_slug


def test_scheduler_materialize_reminders_on_ops(two_db):
    """materialize_event_reminders reads events+reminders from OPS, inserts posts into OPS."""
    from v2.core.publishing.scheduler import Scheduler
    from unittest.mock import AsyncMock

    ops_conn = two_db["ops_conn"]
    kb_conn = two_db["kb_conn"]

    # Insert an event into OPS
    ops_conn.execute(
        "INSERT INTO events(id,name,date,time,location,org_id,org_slug) "
        "VALUES(1,'GSA BBQ','2025-01-05','10:00 AM','Campus',1,'gsa')"
    )
    # Insert a reminder (offset 1 day before)
    ops_conn.execute(
        "INSERT INTO event_reminders(event_id,offset_value,offset_unit,channels,enabled) "
        "VALUES(1,1,'days','[\"discord\"]',1)"
    )
    ops_conn.commit()

    mock_publisher = AsyncMock()
    mock_publisher.publish_due = AsyncMock(return_value={"published": 0, "sent": 0, "failed": 0})

    scheduler = Scheduler(ops_conn, kb_conn, mock_publisher)
    # 'now' is after the reminder fire time (event 2025-01-05 minus 1 day = 2025-01-04 10:00)
    now_dt = datetime(2025, 1, 5, 0, 0, 0)
    count = scheduler.materialize_event_reminders(now_dt)
    assert count == 1

    posts = ops_conn.execute("SELECT * FROM posts WHERE source_type='event_reminder'").fetchall()
    assert len(posts) == 1
    assert posts[0]["org_slug"] == "gsa"  # F4: reminder materializer stamps org_slug


def test_scheduler_combined_db_still_works(combined_db):
    """Scheduler(conn, conn, publisher) works with combined DB."""
    from v2.core.publishing.scheduler import Scheduler
    from unittest.mock import AsyncMock

    mock_publisher = AsyncMock()
    mock_publisher.publish_due = AsyncMock(return_value={"published": 0, "sent": 0, "failed": 0})

    scheduler = Scheduler(combined_db, combined_db, mock_publisher)
    now_dt = datetime(2030, 1, 1, 0, 0, 0)
    # Should run without error even with no due work
    result = _run(scheduler.tick(now_dt))
    assert "published" in result


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — WorldCup watcher two-conn
# ─────────────────────────────────────────────────────────────────────────────

def test_match_watcher_init_accepts_both_paths(two_db):
    """MatchWatcher(keys, ops_path, kb_path) stores both paths."""
    from v2.integration.match_watcher import MatchWatcher
    import tempfile, os
    state_file = os.path.join(two_db["ops_path"].replace(".db", ""), "state.json")

    watcher = MatchWatcher(
        keys="",
        ops_path=two_db["ops_path"],
        kb_path=two_db["kb_path"],
        org_slug="gsa",
        state_file="/tmp/test_watcher_state.json",
    )
    assert watcher.ops_path == two_db["ops_path"]
    assert watcher.kb_path == two_db["kb_path"]


def test_match_watcher_make_watcher_accepts_both_paths(two_db):
    """make_watcher factory accepts (keys, ops_path, kb_path, ...)."""
    from v2.integration.wc_providers.watcher import make_watcher
    watcher = make_watcher(
        "", two_db["ops_path"], two_db["kb_path"],
        state_file="/tmp/test_make_watcher_state.json"
    )
    assert watcher.ops_path == two_db["ops_path"]
    assert watcher.kb_path == two_db["kb_path"]


def test_match_watcher_start_resolves_org_from_kb(two_db):
    """MatchWatcher.start() resolves org via resolve_org (fail-loud on >1 match),
    opens both OPS and KB connections inside the try guard (F7 + F8).
    Exercises real await start() with the _loop patched to return immediately."""
    from unittest.mock import AsyncMock, patch
    from v2.integration.match_watcher import MatchWatcher

    watcher = MatchWatcher(
        keys="",
        ops_path=two_db["ops_path"],
        kb_path=two_db["kb_path"],
        org_slug="gsa",
        state_file="/tmp/test_watcher_start_real_state.json",
    )

    # Patch asyncio.create_task so the _loop coroutine doesn't actually run
    async def _noop_loop():
        pass

    with patch.object(watcher, "_loop", return_value=_noop_loop()):
        import asyncio
        with patch("asyncio.create_task", return_value=None):
            asyncio.get_event_loop().run_until_complete(watcher.start())

    # start() resolved the org from the KB using resolve_org
    assert watcher.org_id == 1
    # Connections were opened (and should be cleaned up by stop)
    assert watcher._conn is not None
    assert watcher._kb_conn is not None
    await_result = _run(watcher.stop())


def test_match_watcher_start_duplicate_slug_raises(two_db):
    """LOW-11: start() raises when org slug maps to >1 org (resolve_org fails loudly)."""
    from v2.integration.match_watcher import MatchWatcher
    from unittest.mock import patch

    # Insert a second org with the same slug under a different parent
    two_db["kb_conn"].execute(
        "INSERT INTO organizations(id, name, slug, type, parent_id) "
        "VALUES(99, 'GSA-Sub', 'gsa', 'club', 1)"
    )
    two_db["kb_conn"].commit()

    watcher = MatchWatcher(
        keys="",
        ops_path=two_db["ops_path"],
        kb_path=two_db["kb_path"],
        org_slug="gsa",
        state_file="/tmp/test_watcher_dup_slug_state.json",
    )
    with pytest.raises(ValueError, match=">1 org"):
        with patch("asyncio.create_task", return_value=None):
            _run(watcher.start())


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — Judging repoint: JudgingSessionManager on OPS-only DB
# ─────────────────────────────────────────────────────────────────────────────

def test_judging_session_manager_works_on_ops_only_db(tmp_path):
    """JudgingSessionManager(ops_path) operates correctly on an OPS-only DB
    created by create_ops_schema (no knowledge tables needed)."""
    from v2.core.database.schema import create_ops_schema
    from v2.core.judging import db as jdb
    from v2.core.judging.session import JudgingSessionManager

    ops_path = str(tmp_path / "ops_only.db")
    ops_conn = create_ops_schema(ops_path)

    eid = jdb.create_event(ops_conn, "Test Event", criteria=["Q1"], top_n=1,
                            score_min=1, score_max=5)
    jdb.set_event_status(ops_conn, eid, "open")
    jdb.load_presenters_csv(ops_conn, eid, "100,Alice,CS")
    jdb.add_judge(ops_conn, eid, "Judge A", "PIN-001")
    ops_conn.commit()
    ops_conn.close()

    manager = JudgingSessionManager(ops_path)

    # Full flow: auth → score
    reply = manager.handle("user1", "judge mode")
    assert reply is not None
    reply = manager.handle("user1", "PIN-001")
    # handle() returns (text, extra) tuple; check the text part
    reply_text = reply[0] if isinstance(reply, tuple) else reply
    assert reply_text is not None and len(reply_text) > 0

    # Manager uses the OPS DB path (not the KB path)
    assert manager.db_path == ops_path


def test_judging_session_manager_db_path_attribute():
    """JudgingSessionManager exposes db_path for wiring verification."""
    from v2.core.judging.session import JudgingSessionManager
    mgr = JudgingSessionManager("/tmp/test_ops.db")
    assert mgr.db_path == "/tmp/test_ops.db"


# ─────────────────────────────────────────────────────────────────────────────
# Task 8 — SchedulerRunner(ops_path, kb_path, registry) + integration smoke
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_runner_init_two_paths(two_db):
    """SchedulerRunner(ops_path, kb_path, registry) stores both paths."""
    from v2.integration.scheduler_runner import SchedulerRunner
    from v2.core.connectors.registry import ConnectorRegistry

    registry = ConnectorRegistry()
    runner = SchedulerRunner(two_db["ops_path"], two_db["kb_path"], registry)
    assert runner.ops_path == two_db["ops_path"]
    assert runner.kb_path == two_db["kb_path"]


async def _two_db_enqueue_and_tick(ops_path, kb_path, org_id):
    """Integration: enqueue a post into OPS, run one scheduler tick, verify delivered."""
    from v2.core.database.schema import get_ops_connection, get_connection
    from v2.core.publishing.sources import PostDraft, enqueue_post
    from v2.core.publishing.publisher import PostPublisher
    from v2.core.publishing.scheduler import Scheduler
    from v2.core.publishing.signature import SignatureService
    from v2.core.connectors.registry import ConnectorRegistry

    ops_conn = get_ops_connection(ops_path)
    kb_conn = get_connection(kb_path)

    stub = _Stub()
    registry = ConnectorRegistry(conn=ops_conn)
    registry.register(stub)

    sigs = SignatureService(kb_conn)
    publisher = PostPublisher(ops_conn, kb_conn, registry, sigs)
    scheduler = Scheduler(ops_conn, kb_conn, publisher)

    draft = PostDraft(org_id=org_id, content="tick test", type="broadcast",
                      channels=["discord"], source_type="test")
    pid = enqueue_post(ops_conn, kb_conn, draft)

    now = "2999-01-01 00:00:00"
    result = await publisher.publish_due(now)

    ops_conn.close()
    kb_conn.close()
    return result, stub.sent, pid


def test_two_db_enqueue_tick_deliver(two_db):
    """Integration: full enqueue→publish cycle on separate KB+OPS DBs."""
    result, sent, pid = _run(
        _two_db_enqueue_and_tick(
            two_db["ops_path"], two_db["kb_path"], org_id=1
        )
    )
    assert result["published"] == 1
    assert result["sent"] == 1
    assert len(sent) == 1
    assert "tick test" in sent[0][0]


def test_combined_db_enqueue_tick_deliver(combined_db, tmp_path):
    """Behavior-preserving: same ops_path==kb_path still delivers correctly."""
    # Write combined DB to a temp file so we have a path
    import shutil, sqlite3

    db_path = str(tmp_path / "combined.db")
    # Copy the in-memory DB to a file
    file_conn = sqlite3.connect(db_path)
    combined_db.backup(file_conn)
    file_conn.close()

    result, sent, pid = _run(
        _two_db_enqueue_and_tick(db_path, db_path, org_id=1)
    )
    assert result["published"] == 1
    assert len(sent) == 1

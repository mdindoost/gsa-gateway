"""Tests for the generator post-sources contract (PostDraft + enqueue_post)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import PostDraft, EnqueueError, enqueue_post


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    c.commit()
    return c


def test_enqueue_inserts_scheduled_row(conn):
    draft = PostDraft(org_id=2, content="Hello world", type="broadcast",
                      channels=["discord"], source_type="test")
    pid = enqueue_post(conn, draft)
    row = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["content"] == "Hello world"
    assert row["org_id"] == 2
    assert row["source_type"] == "test"


def test_rejects_unknown_org(conn):
    with pytest.raises(EnqueueError, match="does not exist"):
        enqueue_post(conn, PostDraft(org_id=999, content="x", type="broadcast"))


def test_rejects_inactive_org(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type,is_active) "
                 "VALUES(3,'Dead','dead','club',0)")
    with pytest.raises(EnqueueError, match="not active"):
        enqueue_post(conn, PostDraft(org_id=3, content="x", type="broadcast"))


def test_rejects_empty_content(conn):
    with pytest.raises(EnqueueError, match="content is empty"):
        enqueue_post(conn, PostDraft(org_id=2, content="   ", type="broadcast"))


def test_rejects_oversized_content(conn):
    with pytest.raises(EnqueueError, match="exceeds"):
        enqueue_post(conn, PostDraft(org_id=2, content="a" * 5000, type="broadcast"))


def test_rejects_unknown_type(conn):
    with pytest.raises(EnqueueError, match="not in allowed"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="haxx"))


def test_rejects_unknown_channel(conn):
    with pytest.raises(EnqueueError, match="unknown channels"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     channels=["myspace"]))


def test_rejects_bad_scheduled_for(conn):
    with pytest.raises(EnqueueError, match="scheduled_for"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     scheduled_for="next tuesday"))


def test_rejects_unserializable_metadata(conn):
    with pytest.raises(EnqueueError, match="JSON"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     metadata={"bad": {1, 2, 3}}))


def test_dedup_returns_existing_id(conn):
    d = PostDraft(org_id=2, content="same content", type="broadcast", source_type="dup")
    first = enqueue_post(conn, d)
    second = enqueue_post(conn, d)
    assert first == second
    n = conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='dup'").fetchone()[0]
    assert n == 1


def test_dedup_on_source_id(conn):
    d1 = PostDraft(org_id=2, content="v1", type="broadcast",
                   source_type="event", source_id=7)
    d2 = PostDraft(org_id=2, content="v2 different content", type="broadcast",
                   source_type="event", source_id=7)
    first = enqueue_post(conn, d1)
    second = enqueue_post(conn, d2)   # same source_id -> dedup hit despite different content
    assert first == second


def test_rejects_oversized_title(conn):
    with pytest.raises(EnqueueError, match="title exceeds"):
        enqueue_post(conn, PostDraft(org_id=2, content="x", type="broadcast",
                                     title="t" * 300))


import asyncio
from v2.core.publishing.sources import SourceRunner, MAX_PER_TICK


class _FakeSource:
    name = "fake"
    def __init__(self, drafts):
        self._drafts = drafts
        self.calls = 0
    async def poll(self):
        self.calls += 1
        if isinstance(self._drafts, Exception):
            raise self._drafts
        return self._drafts


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_runner_enqueues_polled_drafts(conn):
    src = _FakeSource([PostDraft(org_id=2, content="from source", type="broadcast",
                                 source_type="fake")])
    runner = SourceRunner(conn, src)
    n = _run(runner.run_once())
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='fake'").fetchone()[0] == 1


def test_runner_isolates_a_throwing_source(conn):
    runner = SourceRunner(conn, _FakeSource(RuntimeError("boom")))
    # must not raise — a bad source cannot kill the tick
    n = _run(runner.run_once())
    assert n == 0


def test_runner_skips_invalid_drafts_but_keeps_good_ones(conn):
    drafts = [
        PostDraft(org_id=2, content="ok", type="broadcast", source_type="fake"),
        PostDraft(org_id=2, content="", type="broadcast", source_type="fake"),  # invalid
    ]
    runner = SourceRunner(conn, _FakeSource(drafts))
    n = _run(runner.run_once())
    assert n == 1


def test_runner_enforces_flood_cap(conn):
    many = [PostDraft(org_id=2, content=f"msg {i}", type="broadcast", source_type="flood")
            for i in range(MAX_PER_TICK + 10)]
    runner = SourceRunner(conn, _FakeSource(many))
    n = _run(runner.run_once())
    assert n == MAX_PER_TICK

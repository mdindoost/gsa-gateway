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

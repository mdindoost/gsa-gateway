import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import PostDraft, enqueue_post


def test_enqueue_persists_delete_at():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()
    pid = enqueue_post(conn, PostDraft(org_id=1, content="hi", channels=["discord"],
                                       delete_at="2026-06-25 00:00:00"))
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["delete_at"] == "2026-06-25 00:00:00"
    conn.close()


def test_enqueue_delete_at_defaults_none():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()
    pid = enqueue_post(conn, PostDraft(org_id=1, content="hi", channels=["discord"]))
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["delete_at"] is None
    conn.close()

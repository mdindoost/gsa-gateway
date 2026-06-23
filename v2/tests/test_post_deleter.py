import sys
import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.connectors.base import DeliveryResult
from v2.core.publishing.deleter import PostDeleter


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


class _FakeRegistry:
    def __init__(self, result_by_platform):
        self.by = result_by_platform
        self.calls = []

    async def delete_delivery(self, platform, channel, message_id):
        self.calls.append((platform, channel, message_id))
        return self.by[platform]


def _seed(conn, delete_at="2000-01-01 00:00:00", status="sent"):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    conn.execute("INSERT INTO posts(id,org_id,type,content,channels,status,delete_at) "
                 "VALUES(1,1,'worldcup','hi','[\"discord\"]',?,?)", (status, delete_at))
    conn.execute("INSERT INTO post_deliveries(id,post_id,platform,channel,message_id,status) "
                 "VALUES(1,1,'discord','gsa','999','success')")
    conn.commit()


def test_due_post_deletes_discord_and_stamps():
    conn = create_all(":memory:")
    _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord", message_id="999")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["deleted"] == 1
    d = conn.execute("SELECT delete_status, deleted_at FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "deleted" and d["deleted_at"] is not None
    p = conn.execute("SELECT deleted_at FROM posts WHERE id=1").fetchone()
    assert p["deleted_at"] is not None
    conn.close()


def test_not_due_is_skipped():
    conn = create_all(":memory:")
    _seed(conn, delete_at="2999-01-01 00:00:00")
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["posts"] == 0 and reg.calls == []
    conn.close()


def test_unsupported_marks_delivery_not_post_failure():
    conn = create_all(":memory:")
    _seed(conn)
    conn.execute("UPDATE post_deliveries SET platform='groupme', message_id='groupme:x:200' WHERE id=1")
    conn.commit()
    reg = _FakeRegistry({"groupme": DeliveryResult(False, "groupme", error="delete unsupported")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["unsupported"] == 1
    d = conn.execute("SELECT delete_status FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "delete_unsupported"
    conn.close()


def test_not_found_counts_as_deleted():
    conn = create_all(":memory:")
    _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(False, "discord", error="Unknown Message (not found)")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["deleted"] == 1
    conn.close()


def test_deleter_issues_no_DELETE_statements():
    # immortal-records guard: trace every SQL the deleter runs; none may be a DELETE.
    conn = create_all(":memory:")
    _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord")})
    seen = []
    conn.set_trace_callback(lambda s: seen.append(s))
    _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    conn.set_trace_callback(None)
    offending = [s for s in seen if "delete from" in s.lower()]
    assert not offending, f"deleter issued DELETE(s): {offending}"
    conn.close()

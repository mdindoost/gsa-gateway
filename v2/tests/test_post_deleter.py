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


class _MidRegistry:
    """Returns a result keyed by message_id (for multi-delivery posts on one platform)."""
    def __init__(self, by_mid):
        self.by_mid = by_mid
        self.calls = []

    async def delete_delivery(self, platform, channel, message_id):
        self.calls.append((platform, channel, message_id))
        return self.by_mid[message_id]


def test_transient_error_bumps_attempts_and_retries():
    conn = create_all(":memory:")
    _seed(conn)
    reg = _FakeRegistry({"discord": DeliveryResult(False, "discord", error="503 service unavailable")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["deleted"] == 0 and out["failed"] == 0
    d = conn.execute("SELECT delete_status, delete_attempts, delete_error FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] is None            # left NULL to retry next tick
    assert d["delete_attempts"] == 1
    assert "503" in d["delete_error"]
    p = conn.execute("SELECT deleted_at FROM posts WHERE id=1").fetchone()
    assert p["deleted_at"] is None               # rollup blocked while a delivery is still retrying
    conn.close()


def test_attempts_cap_marks_delete_failed():
    conn = create_all(":memory:")
    _seed(conn)
    conn.execute("UPDATE post_deliveries SET delete_attempts=4 WHERE id=1")  # one below the cap
    conn.commit()
    reg = _FakeRegistry({"discord": DeliveryResult(False, "discord", error="503")})
    out = _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert out["failed"] == 1
    d = conn.execute("SELECT delete_status, delete_attempts FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "delete_failed" and d["delete_attempts"] == 5
    p = conn.execute("SELECT deleted_at FROM posts WHERE id=1").fetchone()
    assert p["deleted_at"] is not None           # all deliveries terminal now -> rolled up
    conn.close()


def test_failed_send_is_not_applicable_no_registry_call():
    conn = create_all(":memory:")
    _seed(conn)
    conn.execute("UPDATE post_deliveries SET status='failed' WHERE id=1")  # was never delivered
    conn.commit()
    reg = _FakeRegistry({"discord": DeliveryResult(True, "discord")})
    _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert reg.calls == []                        # nothing to unsend
    d = conn.execute("SELECT delete_status FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "not_applicable"
    conn.close()


def test_telegram_broadcast_sentinel_is_not_applicable():
    conn = create_all(":memory:")
    _seed(conn)
    conn.execute("UPDATE post_deliveries SET platform='telegram', message_id='telegram-broadcast' WHERE id=1")
    conn.commit()
    reg = _FakeRegistry({"telegram": DeliveryResult(False, "telegram", error="x")})
    _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert reg.calls == []                        # sentinel id is not deletable -> not routed
    d = conn.execute("SELECT delete_status FROM post_deliveries WHERE id=1").fetchone()
    assert d["delete_status"] == "not_applicable"
    conn.close()


def test_transient_delivery_blocks_post_rollup():
    conn = create_all(":memory:")
    _seed(conn)  # delivery id=1: discord, mid 999
    conn.execute("INSERT INTO post_deliveries(id,post_id,platform,channel,message_id,status) "
                 "VALUES(2,1,'discord','gsa','888','success')")
    conn.commit()
    reg = _MidRegistry({"999": DeliveryResult(True, "discord"),
                        "888": DeliveryResult(False, "discord", error="503")})
    _run(PostDeleter(conn, reg).delete_due(now="2025-01-01 00:00:00"))
    assert conn.execute("SELECT delete_status FROM post_deliveries WHERE id=1").fetchone()["delete_status"] == "deleted"
    d2 = conn.execute("SELECT delete_status, delete_attempts FROM post_deliveries WHERE id=2").fetchone()
    assert d2["delete_status"] is None and d2["delete_attempts"] == 1
    p = conn.execute("SELECT deleted_at FROM posts WHERE id=1").fetchone()
    assert p["deleted_at"] is None               # one delivery still retrying -> post not rolled up
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

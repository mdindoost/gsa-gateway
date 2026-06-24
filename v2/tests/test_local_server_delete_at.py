import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.local_server import GatewayHandler


def _org(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()


def test_post_post_stores_delete_at():
    conn = create_all(":memory:")
    _org(conn)
    # _post_post doesn't touch `self` for a non-event post -> a dummy self is fine.
    out = GatewayHandler._post_post(object(), conn, {
        "org_id": 1, "type": "broadcast", "content": "hi",
        "channels": ["discord"], "delete_at": "2026-06-25 00:00:00"})
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (out["post_id"],)).fetchone()
    assert row["delete_at"] == "2026-06-25 00:00:00"
    conn.close()


def test_post_post_delete_at_optional():
    conn = create_all(":memory:")
    _org(conn)
    out = GatewayHandler._post_post(object(), conn, {
        "org_id": 1, "type": "broadcast", "content": "hi", "channels": ["discord"]})
    row = conn.execute("SELECT delete_at FROM posts WHERE id=?", (out["post_id"],)).fetchone()
    assert row["delete_at"] is None
    conn.close()


def _da(conn, pid):
    return conn.execute("SELECT delete_at FROM posts WHERE id=?", (pid,)).fetchone()["delete_at"]


def test_post_post_clamps_delete_at_beyond_48h():
    conn = create_all(":memory:")
    _org(conn)
    out = GatewayHandler._post_post(object(), conn, {
        "org_id": 1, "type": "broadcast", "content": "hi", "channels": ["telegram"],
        "scheduled_for": "2026-06-23 00:00:00", "delete_at": "2026-06-30 00:00:00"})  # +7d
    assert _da(conn, out["post_id"]) == "2026-06-25 00:00:00"   # clamped to scheduled + 48h
    conn.close()


def test_post_post_keeps_delete_at_within_48h():
    conn = create_all(":memory:")
    _org(conn)
    out = GatewayHandler._post_post(object(), conn, {
        "org_id": 1, "type": "broadcast", "content": "hi", "channels": ["telegram"],
        "scheduled_for": "2026-06-23 00:00:00", "delete_at": "2026-06-24 00:00:00"})  # +24h
    assert _da(conn, out["post_id"]) == "2026-06-24 00:00:00"   # unchanged
    conn.close()


def test_setting_auto_delete_hours_validated_and_upserted():
    import pytest
    conn = create_all(":memory:")
    _org(conn)
    # out of range → rejected
    with pytest.raises(ValueError):
        GatewayHandler._post_setting(object(), conn, {"org_id": 1, "key": "default.auto_delete_hours", "value": "100"})
    with pytest.raises(ValueError):
        GatewayHandler._post_setting(object(), conn, {"org_id": 1, "key": "default.auto_delete_hours", "value": "0"})
    # in range → upserted (row did NOT exist — live-DB case)
    GatewayHandler._post_setting(object(), conn, {"org_id": 1, "key": "default.auto_delete_hours", "value": "12"})
    row = conn.execute("SELECT value, type FROM settings WHERE org_id=1 AND key='default.auto_delete_hours'").fetchone()
    assert row["value"] == "12" and row["type"] == "int"
    conn.close()

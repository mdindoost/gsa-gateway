"""Phase 2: WorldCup posts auto-delete after default.auto_delete_hours (default 24, clamped 1..48),
set on BOTH enqueue sites (_post and _post_preview)."""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import auto_delete_hours


def _org(conn):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.commit()


def test_auto_delete_hours_default_and_clamp():
    conn = create_all(":memory:")
    _org(conn)
    assert auto_delete_hours(conn, 1) == 24          # unset → code default (not seeded by create_all)
    conn.execute("INSERT INTO settings(org_id,key,value,type) VALUES(1,'default.auto_delete_hours','100','int')")
    conn.commit()
    assert auto_delete_hours(conn, 1) == 48          # clamped to Telegram's 48h ceiling
    conn.execute("UPDATE settings SET value='0' WHERE key='default.auto_delete_hours'")
    conn.commit()
    assert auto_delete_hours(conn, 1) == 1           # clamped to min 1
    conn.close()


def _watcher_with_db():
    from v2.integration.match_watcher import MatchWatcher
    w = MatchWatcher([], ":memory:")
    w._conn = create_all(":memory:")
    _org(w._conn)
    w.org_id = 1
    w.channel = "wc"
    return w


def test_worldcup_post_sets_delete_at_about_24h():
    import datetime
    w = _watcher_with_db()
    ev = {"type": "kickoff", "match": {"homeTeam": {"name": "England"}, "awayTeam": {"name": "Ghana"}}}
    w._post(537411, ev)
    row = w._conn.execute("SELECT delete_at FROM posts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["delete_at"] is not None
    da = datetime.datetime.strptime(row["delete_at"], "%Y-%m-%d %H:%M:%S")
    delta_h = (da - datetime.datetime.utcnow()).total_seconds() / 3600
    assert 23 < delta_h < 25                          # ~24h out
    w._conn.close()

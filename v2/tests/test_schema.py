"""Schema + settings-inheritance tests (Step 6 follow-up fixes)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.queries import get_setting, org_ancestors
from v2.core.database.schema import create_all


def test_create_all_includes_events_table():
    conn = create_all(":memory:")
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "events" in tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    # v1-compatible columns + org_id (so NJIT and greenfield match)
    assert {"date", "time", "category", "organizer", "org_id",
            "reminder_sent_7d"} <= cols
    conn.close()


def test_events_table_matches_live_shape():
    # The v2 STRICT `events` DDL was dead code (the live DB's events table is the v1
    # non-STRICT/AUTOINCREMENT shape with announcement_sent/channel_posted). The split-ops
    # build owns `events` in the OPS schema reproducing that LIVE shape + org_slug (spec HIGH-2),
    # so events is intentionally NON-STRICT now. The OPS events shape is asserted in detail by
    # test_schema_split.test_ops_events_is_live_shape; here we just confirm the legacy columns
    # exist on the events table create_all builds.
    conn = create_all(":memory:")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert {"announcement_sent", "channel_posted", "org_slug"} <= cols
    conn.close()


def test_setting_inherits_from_root():
    conn = create_all(":memory:")
    njit = conn.execute(
        "INSERT INTO organizations(name,slug,type) VALUES('NJIT','njit','university')").lastrowid
    gsa = conn.execute(
        "INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,?,?,?)",
        (njit, "GSA", "gsa", "gsa")).lastrowid
    mmi = conn.execute(
        "INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,?,?,?)",
        (njit, "MMI", "mmi", "event_series")).lastrowid
    conn.execute(
        "INSERT INTO settings(org_id,key,value,type) VALUES(?,?,?,?)",
        (njit, "signature.default", "_NJIT_", "string"))
    conn.commit()

    # both children inherit the root default
    assert get_setting(conn, gsa, "signature.default") == "_NJIT_"
    assert get_setting(conn, mmi, "signature.default") == "_NJIT_"
    # but not without inheritance
    assert get_setting(conn, gsa, "signature.default", inherit=False) is None
    # ancestor order is nearest-first
    assert org_ancestors(conn, gsa) == [gsa, njit]
    conn.close()


def test_local_override_beats_inherited():
    conn = create_all(":memory:")
    njit = conn.execute(
        "INSERT INTO organizations(name,slug,type) VALUES('NJIT','njit','university')").lastrowid
    gsa = conn.execute(
        "INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,?,?,?)",
        (njit, "GSA", "gsa", "gsa")).lastrowid
    conn.execute("INSERT INTO settings(org_id,key,value,type) VALUES(?,?,?,?)",
                 (njit, "default.send_time", "09:00", "string"))
    conn.execute("INSERT INTO settings(org_id,key,value,type) VALUES(?,?,?,?)",
                 (gsa, "default.send_time", "17:00", "string"))
    conn.commit()
    assert get_setting(conn, gsa, "default.send_time") == "17:00"  # local wins
    conn.close()

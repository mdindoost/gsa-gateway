"""Build-3 event-projection tests: event_natural_key, derive_event_kb,
_create_event cross-DB, _post_post cross-DB, and the gated re-derive script.

All tests use temp-file or in-memory DBs — NO live-DB writes.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from v2.core.database.schema import create_all, create_knowledge_schema, create_ops_schema


# ── helpers ─────────────────────────────────────────────────────────────────

def _seed_two_db(tmp_path):
    """Create KB + OPS temp DBs with GSA org seeded in KB.
    Returns (kb_conn, ops_conn, kb_path, ops_path).
    """
    kb_path = str(tmp_path / "kb.db")
    ops_path = str(tmp_path / "ops.db")
    kb_conn = create_knowledge_schema(kb_path)
    ops_conn = create_ops_schema(ops_path)
    kb_conn.execute(
        "INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')"
    )
    kb_conn.commit()
    return kb_conn, ops_conn, kb_path, ops_path


def _insert_ops_event(ops_conn, *, name="Spring Social", date="2026-04-10",
                       time="6:00 PM", location="Campus Center", org_slug="gsa",
                       org_id=1):
    ops_conn.execute(
        "INSERT INTO events(name,date,time,location,description,organizer,category,"
        "org_id,org_slug) VALUES(?,?,?,?,?,?,?,?,?)",
        (name, date, time, location, "", "GSA", "general", org_id, org_slug)
    )
    ops_conn.commit()
    return ops_conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def two_db(tmp_path):
    """KB + OPS temp-file DBs with GSA org + settings seeded in KB."""
    kb_conn, ops_conn, kb_path, ops_path = _seed_two_db(tmp_path)
    # Seed minimal settings so handler tests work
    for row in [
        (1, "signature.default",          "_GSA_",         "string"),
        (1, "default.platforms",          '["discord"]',   "json"),
        (1, "default.channel.broadcast",  "gsa-ann",       "string"),
        (1, "org.telegram_channel",       "@GSA",          "string"),
    ]:
        kb_conn.execute(
            "INSERT OR IGNORE INTO settings(org_id,key,value,type) VALUES(?,?,?,?)", row
        )
    kb_conn.commit()
    yield {"kb_conn": kb_conn, "ops_conn": ops_conn,
           "kb_path": kb_path, "ops_path": ops_path}
    kb_conn.close()
    ops_conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — event_natural_key
# ─────────────────────────────────────────────────────────────────────────────

def test_event_natural_key_stable_under_whitespace_noise():
    from v2.core.publishing.event_projection import event_natural_key
    k1 = event_natural_key("Spring Social", "2026-04-10")
    k2 = event_natural_key("  Spring Social  ", "2026-04-10")
    k3 = event_natural_key("SPRING SOCIAL", "2026-04-10")
    k4 = event_natural_key("spring  social", "2026-04-10")
    # Leading/trailing whitespace and case must not change the key
    assert k1 == k2
    assert k1 == k3
    # But collapsed vs multi-space must also normalize
    assert k1 == k4


def test_event_natural_key_differs_by_date():
    from v2.core.publishing.event_projection import event_natural_key
    k1 = event_natural_key("Spring Social", "2026-04-10")
    k2 = event_natural_key("Spring Social", "2026-04-11")
    assert k1 != k2


def test_event_natural_key_differs_by_name():
    from v2.core.publishing.event_projection import event_natural_key
    k1 = event_natural_key("Spring Social", "2026-04-10")
    k2 = event_natural_key("Fall Gala",     "2026-04-10")
    assert k1 != k2


def test_event_natural_key_is_a_string():
    from v2.core.publishing.event_projection import event_natural_key
    k = event_natural_key("Test Event", "2026-01-01")
    assert isinstance(k, str)
    assert len(k) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — derive_event_kb creates an event_info item (GSA-only)
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_creates_event_info_for_gsa_event(two_db):
    from v2.core.publishing.event_projection import derive_event_kb, event_natural_key

    event_id = _insert_ops_event(
        two_db["ops_conn"],
        name="Spring Social", date="2026-04-10", time="6:00 PM",
        location="Campus Center", org_slug="gsa"
    )

    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    rows = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='event_info'"
    ).fetchall()
    assert len(rows) == 1, f"Expected 1 event_info, got {len(rows)}"

    row = rows[0]
    assert row["title"] == "Spring Social"
    assert row["is_active"] == 1

    meta = json.loads(row["metadata"])
    assert meta["derived_from"] == "ops_event"
    assert meta["org_slug"] == "gsa"
    assert meta["ops_event_id"] == event_id
    assert meta["date"] == "2026-04-10"
    assert meta["time"] == "6:00 PM"
    nk = event_natural_key("Spring Social", "2026-04-10")
    assert meta["natural_key"] == nk


def test_derive_skips_non_gsa_event(two_db):
    """Events with org_slug not in org_slugs must not produce a KB item."""
    from v2.core.publishing.event_projection import derive_event_kb

    # Insert a club event (not GSA)
    two_db["ops_conn"].execute(
        "INSERT INTO events(name,date,time,location,description,organizer,category,"
        "org_id,org_slug) VALUES(?,?,?,?,?,?,?,?,?)",
        ("Club Mixer", "2026-04-15", "TBD", "TBD", "", "CS Club", "general", 2, "cs-club")
    )
    two_db["ops_conn"].commit()

    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    rows = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='event_info'"
    ).fetchall()
    assert len(rows) == 0


def test_derive_event_info_content_format(two_db):
    """Content must match the legacy format: name — date at time, location."""
    from v2.core.publishing.event_projection import derive_event_kb

    _insert_ops_event(
        two_db["ops_conn"],
        name="BBQ Bash", date="2026-05-20", time="5:00 PM", location="Lot 7A"
    )

    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    row = two_db["kb_conn"].execute(
        "SELECT content FROM knowledge_items WHERE type='event_info'"
    ).fetchone()
    assert row is not None
    content = row["content"]
    assert "BBQ Bash" in content
    assert "2026-05-20" in content
    assert "5:00 PM" in content
    assert "Lot 7A" in content


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — Idempotency + MED-8 transition match
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_idempotent_on_rerun(two_db):
    """Running derive_event_kb twice must still produce exactly ONE event_info row."""
    from v2.core.publishing.event_projection import derive_event_kb

    _insert_ops_event(two_db["ops_conn"], name="Spring Social", date="2026-04-10")

    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])  # second run

    rows = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='event_info'"
    ).fetchall()
    assert len(rows) == 1, f"Re-derive must not duplicate: got {len(rows)} rows"


def test_derive_matches_legacy_event_id_med8(two_db):
    """MED-8: an existing event_info keyed only on metadata.event_id (legacy format)
    must be matched and NOT duplicated when derive_event_kb runs.
    """
    from v2.core.publishing.event_projection import derive_event_kb

    # Insert OPS event
    event_id = _insert_ops_event(
        two_db["ops_conn"], name="Spring Social", date="2026-04-10"
    )

    # Pre-seed legacy KB event_info keyed only on event_id (no natural_key)
    two_db["kb_conn"].execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
        "VALUES(?,?,?,?,?,?)",
        (1, "event_info", "Spring Social",
         "Spring Social — 2026-04-10 at 6:00 PM, Campus Center.",
         json.dumps({"event_id": event_id, "date": "2026-04-10", "time": "6:00 PM"}),
         "dashboard")
    )
    two_db["kb_conn"].commit()

    # Run derive — must NOT create a second event_info
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    rows = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='event_info'"
    ).fetchall()
    assert len(rows) == 1, (
        f"MED-8 transition: must match legacy event_id and not duplicate. Got {len(rows)}"
    )

    # The existing row's metadata should now include natural_key (back-filled)
    meta = json.loads(rows[0]["metadata"])
    assert "natural_key" in meta


def test_derive_zero_new_rows_on_already_derived_db(two_db):
    """Reject criterion #7: re-derive over a DB that already has derived items
    yields 0 new event_info rows."""
    from v2.core.publishing.event_projection import derive_event_kb

    _insert_ops_event(two_db["ops_conn"], name="Spring Social", date="2026-04-10")
    _insert_ops_event(two_db["ops_conn"], name="Fall Gala", date="2026-09-15")

    # First derive
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])
    count_before = two_db["kb_conn"].execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE type='event_info'"
    ).fetchone()[0]

    # Second derive — must add 0 rows
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])
    count_after = two_db["kb_conn"].execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE type='event_info'"
    ).fetchone()[0]

    assert count_after == count_before, (
        f"Re-derive added rows: before={count_before}, after={count_after}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — Reconcile removed / renamed events
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_deactivates_removed_event(two_db):
    """Deleting an OPS event and re-deriving must deactivate the stale KB item."""
    from v2.core.publishing.event_projection import derive_event_kb

    event_id = _insert_ops_event(two_db["ops_conn"], name="Spring Social", date="2026-04-10")

    # First derive
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])
    ki_id = two_db["kb_conn"].execute(
        "SELECT id FROM knowledge_items WHERE type='event_info'"
    ).fetchone()["id"]

    # Remove the OPS event
    two_db["ops_conn"].execute("DELETE FROM events WHERE id=?", (event_id,))
    two_db["ops_conn"].commit()

    # Re-derive — stale KB item must be deactivated
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    row = two_db["kb_conn"].execute(
        "SELECT is_active FROM knowledge_items WHERE id=?", (ki_id,)
    ).fetchone()
    assert row["is_active"] == 0, "Stale KB item must be deactivated after event removal"


def test_derive_handles_renamed_event(two_db):
    """Renaming an event: old natural_key item is deactivated; new item is created."""
    from v2.core.publishing.event_projection import derive_event_kb

    event_id = _insert_ops_event(
        two_db["ops_conn"], name="Spring Social", date="2026-04-10"
    )

    # First derive
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    # Rename the event in OPS
    two_db["ops_conn"].execute(
        "UPDATE events SET name=? WHERE id=?", ("Spring Mixer", event_id)
    )
    two_db["ops_conn"].commit()

    # Re-derive
    derive_event_kb(two_db["ops_conn"], two_db["kb_conn"])

    all_items = two_db["kb_conn"].execute(
        "SELECT title, is_active FROM knowledge_items WHERE type='event_info'"
    ).fetchall()

    active = [r for r in all_items if r["is_active"] == 1]
    inactive = [r for r in all_items if r["is_active"] == 0]

    # The old "Spring Social" item is deactivated, a new "Spring Mixer" item is active
    assert len(active) == 1, f"Expected 1 active event_info, got {len(active)}"
    assert active[0]["title"] == "Spring Mixer"
    assert len(inactive) == 1
    assert inactive[0]["title"] == "Spring Social"


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — _create_event cross-DB (OPS-commit-first)
# ─────────────────────────────────────────────────────────────────────────────

def _make_test_handler(ops_path):
    """Instantiate GatewayHandler without HTTP machinery for unit tests.

    The handler internally opens and closes its own OPS connections, so we
    point _ops_conn at the test OPS DB file (returns fresh connections each
    call, matching the real handler's per-request lifecycle).
    The KB connection is passed explicitly as ``conn`` to _create_event /
    _post_post, so we don't need to override _conn() here.
    """
    from v2.local_server import GatewayHandler
    from v2.core.database.schema import get_ops_connection
    handler = GatewayHandler.__new__(GatewayHandler)
    handler._ops_conn = lambda: get_ops_connection(ops_path)
    return handler


class _FailingKBConn:
    """Thin proxy over a real KB conn that raises on knowledge_items INSERT."""

    def __init__(self, real_conn):
        self._real = real_conn

    def execute(self, sql, *args, **kwargs):
        if "knowledge_items" in sql and "INSERT" in sql.upper():
            raise RuntimeError("simulated KB failure")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        pass  # test manages lifecycle

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_create_event_writes_cluster_to_ops_and_event_info_to_kb(two_db):
    """_create_event must write event+post+reminder to OPS and event_info to KB."""
    handler = _make_test_handler(two_db["ops_path"])

    body = {
        "org_id": 1,
        "name":   "Spring Social",
        "date":   "2026-04-10",
        "time":   "6:00 PM",
        "location": "Campus Center",
        "channels": ["discord"],
        "reminders": [{"offset": 1, "unit": "days"}],
    }
    result = handler._create_event(two_db["kb_conn"], body)

    assert result["success"] is True
    assert "event_id" in result

    # OPS: event and announcement post (read via fresh connection to the test DB)
    from v2.core.database.schema import get_ops_connection
    ops_ro = get_ops_connection(two_db["ops_path"])
    try:
        evt = ops_ro.execute(
            "SELECT * FROM events WHERE name=?", ("Spring Social",)
        ).fetchone()
        assert evt is not None
        assert evt["org_slug"] == "gsa"  # stamped from KB

        post = ops_ro.execute(
            "SELECT * FROM posts WHERE type='event_announcement'"
        ).fetchone()
        assert post is not None
        assert post["org_slug"] == "gsa"

        reminder = ops_ro.execute(
            "SELECT * FROM event_reminders WHERE event_id=?", (evt["id"],)
        ).fetchone()
        assert reminder is not None
    finally:
        ops_ro.close()

    # KB: event_info knowledge_item
    ki = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='event_info'"
    ).fetchone()
    assert ki is not None
    meta = json.loads(ki["metadata"])
    assert meta["derived_from"] == "ops_event"
    assert meta["ops_event_id"] == result["event_id"]

    # KB must NOT contain events or posts tables
    import sqlite3 as _sqlite3
    try:
        two_db["kb_conn"].execute("SELECT 1 FROM events LIMIT 1").fetchone()
        assert False, "KB should not have events table in split mode"
    except _sqlite3.OperationalError as exc:
        assert "no such table" in str(exc).lower(), (
            f"Expected 'no such table' error, got: {exc}"
        )


def test_create_event_ops_first_kb_failure_ops_persists(two_db, caplog):
    """OPS-commit-first: if KB write fails, OPS event still persists; warning logged."""
    handler = _make_test_handler(two_db["ops_path"])

    body = {
        "org_id": 1,
        "name":   "Doomed Event",
        "date":   "2026-04-20",
        "channels": [],
    }

    def _failing_derive(*a, **kw):
        raise RuntimeError("simulated KB write failure")

    with patch("v2.local_server.derive_event_kb", _failing_derive):
        with caplog.at_level(logging.WARNING, logger="v2.local_server"):
            result = handler._create_event(two_db["kb_conn"], body)

    # OPS event must still exist despite KB failure
    from v2.core.database.schema import get_ops_connection
    ops_ro = get_ops_connection(two_db["ops_path"])
    try:
        evt = ops_ro.execute(
            "SELECT * FROM events WHERE name='Doomed Event'"
        ).fetchone()
        assert evt is not None, "OPS event must persist even when KB write fails"
    finally:
        ops_ro.close()

    # A warning should have been logged
    assert any(
        "derive" in r.message.lower() or "kb" in r.message.lower()
        or "knowledge" in r.message.lower()
        for r in caplog.records
    ), "Expected a warning log on KB failure"


def test_create_event_org_slug_stamped_from_kb(two_db):
    """org_slug on OPS events/posts must come from KB lookup, not defaulted."""
    two_db["kb_conn"].execute(
        "INSERT INTO organizations(id,name,slug,type) VALUES(2,'YWCC','ywcc','college')"
    )
    two_db["kb_conn"].commit()

    handler = _make_test_handler(two_db["ops_path"])

    body = {"org_id": 2, "name": "YWCC Mixer", "date": "2026-04-10", "channels": []}
    handler._create_event(two_db["kb_conn"], body)

    from v2.core.database.schema import get_ops_connection
    ops_ro = get_ops_connection(two_db["ops_path"])
    try:
        evt = ops_ro.execute(
            "SELECT org_slug FROM events WHERE name='YWCC Mixer'"
        ).fetchone()
        assert evt["org_slug"] == "ywcc"
    finally:
        ops_ro.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — _post_post(add_to_kb) cross-DB
# ─────────────────────────────────────────────────────────────────────────────

def test_post_post_writes_post_to_ops(two_db):
    """_post_post must write the post to OPS, not KB."""
    handler = _make_test_handler(two_db["ops_path"])

    body = {
        "org_id": 1,
        "type": "one_time",
        "content": "Hello from the dashboard",
        "channels": ["discord"],
    }
    result = handler._post_post(two_db["kb_conn"], body)
    assert result["success"] is True

    from v2.core.database.schema import get_ops_connection
    ops_ro = get_ops_connection(two_db["ops_path"])
    try:
        post = ops_ro.execute(
            "SELECT * FROM posts WHERE type='one_time'"
        ).fetchone()
        assert post is not None
        assert post["content"] == "Hello from the dashboard"
        assert post["org_slug"] == "gsa"
    finally:
        ops_ro.close()

    # KB should have no posts table
    import sqlite3 as _sqlite3
    try:
        two_db["kb_conn"].execute("SELECT 1 FROM posts LIMIT 1").fetchone()
        assert False, "KB must not have posts table"
    except _sqlite3.OperationalError as exc:
        assert "no such table" in str(exc).lower(), (
            f"Expected 'no such table' error, got: {exc}"
        )


def test_post_post_add_to_kb_writes_ki_to_kb(two_db):
    """add_to_kb=True must write a knowledge_item to KB after committing post to OPS."""
    handler = _make_test_handler(two_db["ops_path"])

    body = {
        "org_id": 1,
        "type": "one_time",
        "content": "Announcement with KB",
        "channels": ["discord"],
        "add_to_kb": True,
    }
    handler._post_post(two_db["kb_conn"], body)

    ki = two_db["kb_conn"].execute(
        "SELECT * FROM knowledge_items WHERE type='announcement'"
    ).fetchone()
    assert ki is not None
    assert "Announcement with KB" in ki["content"]


def test_post_post_ops_first_ordering(two_db):
    """If KB write fails on add_to_kb, the OPS post must still exist."""
    handler = _make_test_handler(two_db["ops_path"])

    body = {
        "org_id": 1,
        "type": "one_time",
        "content": "Post survives KB failure",
        "channels": [],
        "add_to_kb": True,
    }

    # Simulate KB failure using a proxy conn that raises on knowledge_items INSERT
    fail_kb_conn = _FailingKBConn(two_db["kb_conn"])

    # The KB write fails but the function must not propagate the exception
    # (it's caught internally and logged as a warning)
    result = handler._post_post(fail_kb_conn, body)
    assert result["success"] is True  # still succeeds; KB failure is tolerated

    # OPS post must exist
    from v2.core.database.schema import get_ops_connection
    ops_ro = get_ops_connection(two_db["ops_path"])
    try:
        post = ops_ro.execute(
            "SELECT * FROM posts WHERE content='Post survives KB failure'"
        ).fetchone()
        assert post is not None, "OPS post must persist even when KB write fails"
    finally:
        ops_ro.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — scripts/derive_event_kb.py gated re-derive
# ─────────────────────────────────────────────────────────────────────────────

def _create_db_pair(tmp_path, n_events=2):
    """Return (kb_path, ops_path) with n_events in OPS and KB seeded."""
    kb_conn, ops_conn, kb_path, ops_path = _seed_two_db(tmp_path)
    for i in range(n_events):
        ops_conn.execute(
            "INSERT INTO events(name,date,time,location,description,organizer,category,"
            "org_id,org_slug) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"Event {i}", f"2026-{i+1:02d}-01", "TBD", "TBD", "", "GSA", "general", 1, "gsa")
        )
    ops_conn.commit()
    kb_conn.close()
    ops_conn.close()
    return kb_path, ops_path


SCRIPT = str(Path(__file__).parents[2] / "scripts" / "derive_event_kb.py")


def test_derive_script_dry_run_writes_nothing(tmp_path):
    """--dry-run (default) must not write any knowledge_items to KB."""
    kb_path, ops_path = _create_db_pair(tmp_path, n_events=2)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--kb", kb_path, "--ops", ops_path],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "dry" in result.stdout.lower() or "plan" in result.stdout.lower() or \
           "would" in result.stdout.lower() or "2" in result.stdout, \
           "Dry-run should report planned derives"

    # No KB items written
    import sqlite3
    conn = sqlite3.connect(kb_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE type='event_info'"
    ).fetchone()[0]
    conn.close()
    assert count == 0, f"Dry-run must not write items; found {count}"


def test_derive_script_commit_derives_all(tmp_path):
    """--commit must derive all GSA events idempotently."""
    kb_path, ops_path = _create_db_pair(tmp_path, n_events=3)

    result = subprocess.run(
        [sys.executable, SCRIPT, "--kb", kb_path, "--ops", ops_path, "--commit"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    import sqlite3
    conn = sqlite3.connect(kb_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE type='event_info'"
    ).fetchone()[0]
    conn.close()
    assert count == 3, f"Expected 3 derived items, got {count}"


def test_derive_script_commit_idempotent_rerun(tmp_path):
    """Running --commit twice must yield 0 net new rows on the second run."""
    kb_path, ops_path = _create_db_pair(tmp_path, n_events=2)

    for _ in range(2):
        subprocess.run(
            [sys.executable, SCRIPT, "--kb", kb_path, "--ops", ops_path, "--commit"],
            capture_output=True, text=True, check=True
        )

    import sqlite3
    conn = sqlite3.connect(kb_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE type='event_info'"
    ).fetchone()[0]
    conn.close()
    assert count == 2, f"Idempotent rerun must yield same count; got {count}"

"""Tests for v2/core/judging/session.py — uses a temp file DB (not :memory:)
because JudgingSessionManager opens its own connections."""
import os
import tempfile

import pytest

from v2.core.database.schema import create_all
from v2.core.judging import db as jdb
from v2.core.judging.session import JudgingSessionManager


@pytest.fixture
def setup():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = create_all(db_path)
    eid = jdb.create_event(conn, "3MRP Test", criteria=["Q1", "Q2"], top_n=1)
    jdb.set_event_status(conn, eid, "open")
    jdb.load_presenters_csv(conn, eid, "100,Jane Smith,CS\n101,Ali Hassan,EE")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    jdb.add_judge(conn, eid, "Bob", "J-002")
    conn.commit()
    conn.close()
    manager = JudgingSessionManager(db_path)
    yield manager, eid, db_path
    os.unlink(db_path)


def _auth(manager, user_id="user1", pin="J-001"):
    manager.handle(user_id, "judge mode")
    manager.handle(user_id, pin)


# ── passthrough ───────────────────────────────────────────────────────────────

def test_idle_passthrough(setup):
    manager, *_ = setup
    resp, consumed = manager.handle("user1", "Who are the GSA officers?")
    assert consumed is False
    assert resp is None


def test_judge_mode_triggers_pin_request(setup):
    manager, *_ = setup
    resp, consumed = manager.handle("user1", "judge mode")
    assert consumed is True
    assert "PIN" in resp


def test_judge_mode_case_insensitive(setup):
    manager, *_ = setup
    resp, consumed = manager.handle("user1", "JUDGE MODE")
    assert consumed is True


# ── authentication ────────────────────────────────────────────────────────────

def test_invalid_pin(setup):
    manager, *_ = setup
    manager.handle("user1", "judge mode")
    resp, consumed = manager.handle("user1", "BADPIN")
    assert consumed is True
    assert "Invalid" in resp


def test_valid_pin_authenticates(setup):
    manager, *_ = setup
    manager.handle("user1", "judge mode")
    resp, consumed = manager.handle("user1", "J-001")
    assert consumed is True
    assert "Amira" in resp


def test_pin_collision_rejected(setup):
    manager, *_ = setup
    # user1 claims J-001
    manager.handle("user1", "judge mode")
    manager.handle("user1", "J-001")
    # user2 tries same PIN
    manager.handle("user2", "judge mode")
    resp, consumed = manager.handle("user2", "J-001")
    assert consumed is True
    assert "Invalid" in resp or "in use" in resp


# ── full scoring flow ─────────────────────────────────────────────────────────

def test_full_scoring_flow(setup):
    manager, eid, db_path = setup
    _auth(manager)
    resp, _ = manager.handle("user1", "100")
    assert "Jane Smith" in resp
    assert "Q1" in resp
    manager.handle("user1", "4")
    resp, _ = manager.handle("user1", "5")
    assert "Review" in resp or "yes" in resp.lower()
    resp, _ = manager.handle("user1", "yes")
    assert "submitted" in resp.lower()

    conn = create_all(db_path)
    j = jdb.get_judge_by_telegram_hash(conn, eid, "user1")
    assert jdb.has_scored(conn, eid, j["id"], 100)
    conn.close()


def test_redo_during_scoring(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "1")          # Q1
    resp, _ = manager.handle("user1", "redo")
    assert "Q1" in resp                   # restarted from Q1


def test_redo_during_confirmation(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "3")          # Q1
    manager.handle("user1", "4")          # Q2 → confirmation
    resp, _ = manager.handle("user1", "redo")
    assert "Q1" in resp


def test_confirmation_shows_all_scores(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "3")
    resp, _ = manager.handle("user1", "5")
    assert "3" in resp and "5" in resp
    assert "Average" in resp


# ── guard rails ───────────────────────────────────────────────────────────────

def test_invalid_score_non_numeric(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    resp, consumed = manager.handle("user1", "great")
    assert consumed is True
    assert "1 to 5" in resp


def test_score_out_of_range(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    resp, consumed = manager.handle("user1", "9")
    assert consumed is True
    assert "1 and 5" in resp


def test_unknown_presenter_number(setup):
    manager, *_ = setup
    _auth(manager)
    resp, consumed = manager.handle("user1", "999")
    assert consumed is True
    assert "not found" in resp.lower()


def test_already_scored_presenter(setup):
    manager, *_ = setup
    # Score 100
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "4")
    manager.handle("user1", "5")
    manager.handle("user1", "yes")
    # Try again
    resp, consumed = manager.handle("user1", "100")
    assert consumed is True
    assert "already" in resp.lower()


# ── resume / logout ───────────────────────────────────────────────────────────

def test_exit_judge_mode(setup):
    manager, *_ = setup
    _auth(manager)
    resp, consumed = manager.handle("user1", "exit judge mode")
    assert consumed is True
    resp2, consumed2 = manager.handle("user1", "Who is the GSA president?")
    assert consumed2 is False


def test_resume_skips_pin_if_already_authenticated(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "exit judge mode")
    resp, consumed = manager.handle("user1", "judge mode")
    assert consumed is True
    assert "PIN" not in resp
    assert "Welcome back" in resp


# ── concurrent judges ─────────────────────────────────────────────────────────

def test_two_judges_independent_sessions(setup):
    manager, *_ = setup
    _auth(manager, "user1", "J-001")
    _auth(manager, "user2", "J-002")
    resp1, _ = manager.handle("user1", "100")
    assert "Jane Smith" in resp1
    resp2, _ = manager.handle("user2", "101")
    assert "Ali Hassan" in resp2

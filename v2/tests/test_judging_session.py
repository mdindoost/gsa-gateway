"""Tests for v2/core/judging/session.py — uses a temp file DB (not :memory:)
because JudgingSessionManager opens its own connections."""
import os
import tempfile

import pytest

os.environ.setdefault("GSA_JUDGING_SCRYPT_N", "64")  # fast scrypt for tests

from v2.core.database.schema import create_all
from v2.core.judging import db as jdb
from v2.core.judging.session import JudgingSessionManager


@pytest.fixture
def setup():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = create_all(db_path)
    eid = jdb.create_event(conn, "3MRP Test", criteria=["Q1", "Q2"], top_n=1,
                            score_min=1, score_max=5)
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


# ── three-state event messages ────────────────────────────────────────────────

def test_judge_mode_when_event_not_open(setup):
    manager, eid, db_path = setup
    conn = create_all(db_path)
    jdb.set_event_status(conn, eid, "setup")
    conn.commit()
    conn.close()
    manager2 = JudgingSessionManager(db_path)
    resp, consumed = manager2.handle("newuser", "judge mode")
    assert consumed is True
    assert "not" in resp.lower() or "opened" in resp.lower() or "setup" in resp.lower()


def test_judge_mode_when_event_closed(setup):
    manager, eid, db_path = setup
    conn = create_all(db_path)
    jdb.set_event_status(conn, eid, "closed")
    conn.commit()
    conn.close()
    manager2 = JudgingSessionManager(db_path)
    resp, consumed = manager2.handle("newuser", "judge mode")
    assert consumed is True
    assert "closed" in resp.lower()


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
    manager.handle("user1", "judge mode")
    manager.handle("user1", "J-001")
    manager.handle("user2", "judge mode")
    resp, consumed = manager.handle("user2", "J-001")
    assert consumed is True
    assert "Invalid" in resp or "in use" in resp


# ── full scoring flow ─────────────────────────────────────────────────────────

def test_full_scoring_flow(setup):
    manager, eid, db_path = setup
    _auth(manager)
    resp, _ = manager.handle("user1", "100")
    assert "Jane Smith" in resp          # confirmation shows name/dept first
    resp, _ = manager.handle("user1", "yes")   # confirm → start scoring
    assert "Q1" in resp
    manager.handle("user1", "4")
    resp, _ = manager.handle("user1", "5")
    # confirmation should show Total: X/Y
    assert "Total:" in resp
    assert "9/10" in resp  # scores 4+5=9, denom = 2 criteria × max 5 = 10
    resp, _ = manager.handle("user1", "yes")
    assert "submitted" in resp.lower()

    conn = create_all(db_path)
    j = jdb.get_judge_by_telegram_hash(conn, eid, "user1")
    assert jdb.has_scored(conn, eid, j["id"], 100)
    conn.close()


# ── confirm presenter before scoring ────────────────────────────────────────────

def test_confirm_presenter_shows_name_then_yes_scores(setup):
    manager, *_ = setup
    _auth(manager)
    resp, _ = manager.handle("user1", "100")
    assert "Jane Smith" in resp and "correct" in resp.lower()  # confirmation, not scoring
    assert "Q1" not in resp
    resp, _ = manager.handle("user1", "yes")
    assert "Q1" in resp                                         # now scoring


def test_confirm_presenter_no_asks_for_number(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    resp, _ = manager.handle("user1", "no")
    assert "enter" in resp.lower() and "number" in resp.lower()
    # back in ready — a fresh number re-confirms
    resp, _ = manager.handle("user1", "101")
    assert "Ali Hassan" in resp


def test_confirm_presenter_wrong_number_corrects(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")            # confirm Jane (wrong)
    resp, _ = manager.handle("user1", "101")  # "I meant 101" → re-confirm
    assert "Ali Hassan" in resp
    resp, _ = manager.handle("user1", "yes")
    assert "Q1" in resp


def test_confirm_presenter_unknown_correction(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    resp, _ = manager.handle("user1", "999")  # correction to a non-existent number
    assert "not found" in resp.lower()


def test_confirmation_shows_total_not_average(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")
    manager.handle("user1", "3")
    resp, _ = manager.handle("user1", "4")
    assert "Total:" in resp
    assert "7/10" in resp   # 3+4=7, denom=2×5=10


def test_redo_during_scoring(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")
    manager.handle("user1", "1")
    resp, _ = manager.handle("user1", "redo")
    assert "Q1" in resp


def test_redo_during_confirmation(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")
    manager.handle("user1", "3")
    manager.handle("user1", "4")
    resp, _ = manager.handle("user1", "redo")
    assert "Q1" in resp


# ── guard rails ───────────────────────────────────────────────────────────────

def test_invalid_score_non_numeric(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")
    resp, consumed = manager.handle("user1", "great")
    assert consumed is True
    assert "1" in resp and "5" in resp


def test_score_out_of_range(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")
    resp, consumed = manager.handle("user1", "9")
    assert consumed is True
    assert "5" in resp


def test_unknown_presenter_number(setup):
    manager, *_ = setup
    _auth(manager)
    resp, consumed = manager.handle("user1", "999")
    assert consumed is True
    assert "not found" in resp.lower()


def test_already_scored_shows_previous_scores(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")        # confirm presenter
    manager.handle("user1", "4")
    manager.handle("user1", "5")
    manager.handle("user1", "yes")        # submit scores
    resp, consumed = manager.handle("user1", "100")
    assert consumed is True
    assert "already" in resp.lower()
    # Should show the previous scores
    assert "4" in resp and "5" in resp
    assert "Total:" in resp


# ── my scores ─────────────────────────────────────────────────────────────────

def test_my_scores_empty(setup):
    manager, *_ = setup
    _auth(manager)
    resp, consumed = manager.handle("user1", "my scores")
    assert consumed is True
    assert "haven't" in resp.lower() or "no" in resp.lower()


def test_my_scores_after_submitting(setup):
    manager, *_ = setup
    _auth(manager)
    manager.handle("user1", "100")
    manager.handle("user1", "yes")        # confirm presenter
    manager.handle("user1", "4")
    manager.handle("user1", "5")
    manager.handle("user1", "yes")        # submit scores
    resp, consumed = manager.handle("user1", "my scores")
    assert consumed is True
    assert "100" in resp
    assert "Jane" in resp


# ── presenter mode ────────────────────────────────────────────────────────────

def test_presenter_mode_registers(setup):
    manager, eid, db_path = setup
    resp, consumed = manager.handle("puser1", "presenter mode")
    assert consumed is True
    assert "number" in resp.lower()
    resp2, consumed2 = manager.handle("puser1", "100")
    assert consumed2 is True
    assert "Jane Smith" in resp2
    assert "100" in resp2

    conn = create_all(db_path)
    p = jdb.get_presenter(conn, eid, 100)
    assert p["is_present"] is True
    conn.close()


def test_presenter_mode_wrong_number(setup):
    manager, *_ = setup
    manager.handle("puser1", "presenter mode")
    resp, consumed = manager.handle("puser1", "999")
    assert consumed is True
    assert "not found" in resp.lower()


def test_presenter_mode_number_already_taken(setup):
    manager, *_ = setup
    # First user claims 100
    manager.handle("puser1", "presenter mode")
    manager.handle("puser1", "100")
    # Second user tries same number
    manager.handle("puser2", "presenter mode")
    resp, _ = manager.handle("puser2", "100")
    assert "already registered" in resp.lower() or "different account" in resp.lower()


def test_presenter_mode_idle_after_registration(setup):
    manager, *_ = setup
    manager.handle("puser1", "presenter mode")
    manager.handle("puser1", "100")
    resp, consumed = manager.handle("puser1", "Who is the GSA president?")
    assert consumed is False


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


# ── audience mode ─────────────────────────────────────────────────────────────

@pytest.fixture
def setup_audience():
    """Event with audience voting open."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = create_all(db_path)
    eid = jdb.create_event(conn, "3MRP Test", criteria=["Q1", "Q2"], top_n=1,
                            score_min=1, score_max=5, audience_top_n=1)
    jdb.set_event_status(conn, eid, "open")
    jdb.set_audience_voting(conn, eid, "open")
    jdb.load_presenters_csv(conn, eid, "100,Jane Smith,CS\n101,Ali Hassan,EE")
    jdb.add_judge(conn, eid, "Amira", "J-001")
    conn.commit()
    conn.close()
    manager = JudgingSessionManager(db_path)
    yield manager, eid, db_path
    os.unlink(db_path)


def test_audience_mode_prompts_for_number(setup_audience):
    manager, *_ = setup_audience
    resp, consumed = manager.handle("anon1", "audience mode")
    assert consumed is True
    assert "number" in resp.lower() or "presenter" in resp.lower()


def test_audience_mode_full_vote_flow(setup_audience):
    manager, eid, db_path = setup_audience
    manager.handle("anon1", "audience mode")
    manager.handle("anon1", "100")
    resp, consumed = manager.handle("anon1", "yes")
    assert consumed is True
    assert "Jane Smith" in resp or "100" in resp

    conn = create_all(db_path)
    v = jdb.get_vote(conn, eid, "anon1")
    assert v is not None
    assert v["presenter_number"] == 100
    conn.close()


def test_audience_mode_idle_after_vote(setup_audience):
    manager, *_ = setup_audience
    manager.handle("anon1", "audience mode")
    manager.handle("anon1", "100")
    manager.handle("anon1", "yes")
    # Should be back to idle — GSA question passes through
    resp, consumed = manager.handle("anon1", "Who is the GSA president?")
    assert consumed is False


def test_audience_mode_change_number_before_yes(setup_audience):
    manager, eid, db_path = setup_audience
    manager.handle("anon1", "audience mode")
    manager.handle("anon1", "100")        # select 100
    manager.handle("anon1", "101")        # change to 101
    manager.handle("anon1", "yes")        # confirm 101

    conn = create_all(db_path)
    v = jdb.get_vote(conn, eid, "anon1")
    assert v["presenter_number"] == 101
    conn.close()


def test_audience_mode_unknown_number(setup_audience):
    manager, *_ = setup_audience
    manager.handle("anon1", "audience mode")
    resp, consumed = manager.handle("anon1", "999")
    assert consumed is True
    assert "not found" in resp.lower()


def test_audience_mode_shows_previous_vote(setup_audience):
    manager, eid, db_path = setup_audience
    # Vote once
    manager.handle("anon1", "audience mode")
    manager.handle("anon1", "100")
    manager.handle("anon1", "yes")
    # Re-enter audience mode
    resp, consumed = manager.handle("anon1", "audience mode")
    assert consumed is True
    assert "previously" in resp.lower() or "100" in resp


def test_judge_returns_to_judge_mode_after_vote(setup_audience):
    manager, *_ = setup_audience
    # Authenticate as judge
    manager.handle("judge1", "judge mode")
    manager.handle("judge1", "J-001")
    # Switch to audience mode
    manager.handle("judge1", "audience mode")
    manager.handle("judge1", "100")
    resp, _ = manager.handle("judge1", "yes")
    # Should return to judge mode
    assert "Judge Mode" in resp or "judge" in resp.lower()
    # Next message should be consumed as judge
    resp2, consumed2 = manager.handle("judge1", "101")
    assert consumed2 is True
    assert "Ali Hassan" in resp2


def test_audience_voting_closed_message(setup_audience):
    manager, eid, db_path = setup_audience
    conn = create_all(db_path)
    jdb.set_audience_voting(conn, eid, "closed")
    conn.commit()
    conn.close()
    manager2 = JudgingSessionManager(db_path)
    resp, consumed = manager2.handle("anon2", "audience mode")
    assert consumed is True
    assert "not active" in resp.lower() or "not" in resp.lower()

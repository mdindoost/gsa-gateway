"""Tests for the mode projection added to JudgingSessionManager (mode_of + is_trigger).

These are the read-only additions that let the unified ModeRegistry DERIVE the judging
mode from the live session state (no mirror). They must reflect every state transition
automatically — so we drive real flows and assert the projection tracks state.
"""
import os
import tempfile

import pytest

os.environ.setdefault("GSA_JUDGING_SCRYPT_N", "64")

from bot.core.modes import Mode
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
    # enable audience voting for the audience-mode tests
    jdb.set_audience_voting(conn, eid, "open")
    conn.commit()
    conn.close()
    manager = JudgingSessionManager(db_path)
    yield manager, eid, db_path
    os.unlink(db_path)


# ── is_trigger ────────────────────────────────────────────────────────────────

def test_is_trigger_recognizes_each_mode(setup):
    manager, *_ = setup
    assert manager.is_trigger("judge mode")
    assert manager.is_trigger("presenter mode")
    assert manager.is_trigger("audience mode")
    assert manager.is_trigger("JUDGE MODE")          # case-insensitive (re.search)


def test_is_trigger_false_for_normal_text(setup):
    manager, *_ = setup
    assert not manager.is_trigger("who are the GSA officers?")
    assert not manager.is_trigger("104")


# ── mode_of: idle ─────────────────────────────────────────────────────────────

def test_mode_of_idle_is_none(setup):
    manager, *_ = setup
    assert manager.mode_of("nobody") is None


# ── mode_of tracks judge flow ────────────────────────────────────────────────

def test_mode_of_judge_after_entering(setup):
    manager, *_ = setup
    manager.handle("u1", "judge mode")               # -> awaiting_pin
    assert manager.mode_of("u1") == Mode.JUDGE
    manager.handle("u1", "J-001")                    # -> ready
    assert manager.mode_of("u1") == Mode.JUDGE


def test_mode_of_judge_during_scoring(setup):
    manager, *_ = setup
    manager.handle("u1", "judge mode")
    manager.handle("u1", "J-001")
    manager.handle("u1", "100")                      # confirming_presenter
    assert manager.mode_of("u1") == Mode.JUDGE
    manager.handle("u1", "yes")                      # scoring
    assert manager.mode_of("u1") == Mode.JUDGE


def test_mode_of_returns_none_after_logout(setup):
    manager, *_ = setup
    manager.handle("u1", "judge mode")
    manager.handle("u1", "J-001")
    assert manager.mode_of("u1") == Mode.JUDGE
    manager.handle("u1", "exit judge mode")
    assert manager.mode_of("u1") is None             # derived: pop -> idle -> None


# ── mode_of tracks presenter flow ────────────────────────────────────────────

def test_mode_of_presenter_then_none_after_register(setup):
    manager, *_ = setup
    manager.handle("u2", "presenter mode")           # presenter_awaiting_number
    assert manager.mode_of("u2") == Mode.PRESENTER
    manager.handle("u2", "100")                      # registers -> pops to idle
    assert manager.mode_of("u2") is None             # the L253 pop is auto-reflected


# ── mode_of tracks audience flow ─────────────────────────────────────────────

def test_mode_of_audience_then_none_after_vote(setup):
    manager, *_ = setup
    manager.handle("u3", "audience mode")            # audience_ready
    assert manager.mode_of("u3") == Mode.AUDIENCE
    manager.handle("u3", "100")                      # audience_confirming
    assert manager.mode_of("u3") == Mode.AUDIENCE
    manager.handle("u3", "yes")                      # cast vote, non-judge -> idle
    assert manager.mode_of("u3") is None


def test_mode_of_judge_returns_to_judge_after_audience_vote(setup):
    # A judge who votes in audience mode auto-returns to JUDGE (pre_audience_state).
    manager, *_ = setup
    manager.handle("u1", "judge mode")
    manager.handle("u1", "J-001")                    # ready (JUDGE)
    manager.handle("u1", "audience mode")            # audience_ready (AUDIENCE)
    assert manager.mode_of("u1") == Mode.AUDIENCE
    manager.handle("u1", "100")                      # audience_confirming
    manager.handle("u1", "yes")                      # vote -> restore to ready
    assert manager.mode_of("u1") == Mode.JUDGE       # derived from restored state

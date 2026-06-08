"""Tests for the ConversationManager service."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.services.conversation import ConversationManager, ConversationSession


@pytest.fixture
def manager() -> ConversationManager:
    return ConversationManager(timeout_minutes=30, max_turns=3)


def test_session_created_on_first_message(manager):
    session = manager.get_or_create_session("user_001")
    assert session is not None
    assert session.user_id == "user_001"
    assert len(session.turns) == 0


def test_session_expires_after_timeout(manager):
    manager.get_or_create_session("user_002")
    # Manually backdate last_active past timeout
    session = manager.sessions["user_002"]
    session.last_active = datetime.now(timezone.utc) - timedelta(minutes=31)
    # Now get_session should return None
    result = manager.get_session("user_002")
    assert result is None
    assert "user_002" not in manager.sessions


def test_max_turns_enforced(manager):
    user_id = "user_003"
    # Add 8 turns (max_turns=3 means max 6 stored)
    for i in range(8):
        role = "user" if i % 2 == 0 else "assistant"
        manager.add_turn(user_id, role, f"message {i}")
    session = manager.sessions[user_id]
    assert len(session.turns) <= manager.max_turns * 2


def test_clear_session_removes_history(manager):
    user_id = "user_004"
    manager.add_turn(user_id, "user", "hello")
    manager.add_turn(user_id, "assistant", "hi there")
    manager.clear_session(user_id)
    assert manager.get_session(user_id) is None


def test_history_formatted_for_prompt(manager):
    user_id = "user_005"
    manager.add_turn(user_id, "user", "What is the travel award?")
    manager.add_turn(user_id, "assistant", "The travel award gives up to $500.")
    result = manager.format_history_for_prompt(user_id)
    assert "Previous conversation:" in result
    assert "Student:" in result
    assert "GSA Gateway:" in result
    assert "travel award" in result.lower()


def test_multiple_users_isolated(manager):
    manager.add_turn("user_a", "user", "Question from A")
    manager.add_turn("user_b", "user", "Question from B")

    history_a = manager.get_history("user_a")
    history_b = manager.get_history("user_b")

    assert len(history_a) == 1
    assert len(history_b) == 1
    assert history_a[0]["content"] == "Question from A"
    assert history_b[0]["content"] == "Question from B"
    # A's history should not appear in B's
    all_b_content = " ".join(t["content"] for t in history_b)
    assert "Question from A" not in all_b_content


def test_get_history_returns_empty_for_unknown_user(manager):
    history = manager.get_history("never_seen_user")
    assert history == []


def test_get_stats(manager):
    manager.add_turn("u1", "user", "hi")
    manager.add_turn("u1", "assistant", "hello")
    manager.add_turn("u2", "user", "test")
    stats = manager.get_stats()
    assert stats["active_sessions"] == 2
    assert stats["total_turns"] == 3


def test_add_turn_updates_last_active(manager):
    user_id = "user_la"
    before = datetime.now(timezone.utc)
    manager.add_turn(user_id, "user", "ping")
    session = manager.sessions[user_id]
    assert session.last_active >= before


# ── Mode field ────────────────────────────────────────────────────────────────

def test_session_default_mode_is_gsa(manager):
    session = manager.get_or_create_session("user_mode_1")
    assert session.mode == "gsa"


def test_get_mode_returns_gsa_for_unknown_user(manager):
    assert manager.get_mode("never_seen") == "gsa"


def test_set_mode_to_free(manager):
    manager.set_mode("user_mode_2", "free")
    assert manager.get_mode("user_mode_2") == "free"


def test_set_mode_back_to_gsa(manager):
    manager.set_mode("user_mode_3", "free")
    manager.set_mode("user_mode_3", "gsa")
    assert manager.get_mode("user_mode_3") == "gsa"


def test_mode_resets_on_clear_session(manager):
    manager.set_mode("user_mode_4", "free")
    manager.clear_session("user_mode_4")
    assert manager.get_mode("user_mode_4") == "gsa"


def test_mode_is_per_user(manager):
    manager.set_mode("user_mode_5", "free")
    assert manager.get_mode("user_mode_6") == "gsa"

"""Tests for the SQLite database service, including privacy hashing."""

import hashlib

import pytest

from bot.services.database import Database, hash_user_id


class TestPrivacyHashing:
    """Verify that user IDs are hashed and never stored in plain text."""

    def test_hash_produces_hex_string(self) -> None:
        result = hash_user_id(123456789)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest is 64 chars

    def test_hash_is_deterministic(self) -> None:
        assert hash_user_id(111) == hash_user_id(111)

    def test_different_ids_produce_different_hashes(self) -> None:
        assert hash_user_id(111) != hash_user_id(222)

    def test_hash_matches_manual_sha256(self) -> None:
        uid = 987654321
        expected = hashlib.sha256(str(uid).encode()).hexdigest()
        assert hash_user_id(uid) == expected

    def test_string_and_int_ids_equivalent(self) -> None:
        assert hash_user_id(42) == hash_user_id("42")


class TestDatabaseInit:
    def test_tables_created(self, db: Database) -> None:
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row[0] for row in tables}
        assert "questions" in names
        assert "initiatives" in names
        assert "feedback" in names
        assert "events_log" in names
        assert "admin_actions" in names

    def test_double_init_is_idempotent(self, db: Database) -> None:
        """Calling init_tables twice should not raise."""
        db.init_tables()


class TestQuestionsTable:
    def test_log_question_returns_id(self, db: Database) -> None:
        row_id = db.log_question(
            user_id=1,
            question="What is GSA?",
            matched_topic="about_gsa",
            confidence=92.5,
            guild_id=99,
        )
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_log_question_stores_data(self, db: Database) -> None:
        db.log_question(1, "test question", "topic", 80.0, 99)
        rows = db.get_all_questions()
        assert len(rows) == 1
        assert rows[0]["question_text"] == "test question"
        assert rows[0]["matched_topic"] == "topic"

    def test_raw_user_id_not_stored(self, db: Database) -> None:
        db.log_question(999888777, "q", None, None, None)
        rows = db.conn.execute("SELECT * FROM questions").fetchall()
        for row in rows:
            # Verify the raw user ID is not present anywhere in the row
            assert "999888777" not in str(dict(row))

    def test_null_topic_allowed(self, db: Database) -> None:
        row_id = db.log_question(1, "unanswered q", None, None, None)
        assert row_id >= 1


class TestInitiativesTable:
    def test_log_initiative_returns_id(self, db: Database) -> None:
        row_id = db.log_initiative(
            user_id=2,
            title="Study Groups",
            description="Weekly peer study sessions",
            category="academic",
            include_contact=False,
            contact_info=None,
            guild_id=99,
        )
        assert row_id >= 1

    def test_anonymous_initiative_has_null_contact(self, db: Database) -> None:
        db.log_initiative(2, "Title", "Desc", "social", False, "email@test.com", 99)
        rows = db.get_all_initiatives()
        # contact_info must be NULL when include_contact is False
        assert rows[0]["contact_info"] is None

    def test_initiative_with_contact_stores_info(self, db: Database) -> None:
        db.log_initiative(3, "T", "D", "career", True, "student@njit.edu", 99)
        rows = db.get_all_initiatives()
        assert rows[0]["contact_info"] == "student@njit.edu"

    def test_default_status_is_pending(self, db: Database) -> None:
        db.log_initiative(4, "T", "D", "other", False, None, None)
        rows = db.get_all_initiatives()
        assert rows[0]["status"] == "pending"


class TestFeedbackTable:
    def test_log_feedback_returns_id(self, db: Database) -> None:
        row_id = db.log_feedback(5, "Great events this semester!", 99)
        assert row_id >= 1

    def test_feedback_stored(self, db: Database) -> None:
        db.log_feedback(5, "More coffee please", 99)
        rows = db.get_all_feedback()
        assert rows[0]["message"] == "More coffee please"

    def test_raw_user_id_not_in_feedback(self, db: Database) -> None:
        db.log_feedback(111222333, "test msg", None)
        rows = db.conn.execute("SELECT * FROM feedback").fetchall()
        for row in rows:
            assert "111222333" not in str(dict(row))


class TestAdminActions:
    def test_log_admin_action(self, db: Database) -> None:
        db.log_admin_action(officer_id=10, action="admin_stats", detail=None)
        rows = db.conn.execute("SELECT * FROM admin_actions").fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "admin_stats"

    def test_events_log(self, db: Database) -> None:
        db.log_event_action("Research Mixer", "viewed", 10)
        rows = db.conn.execute("SELECT * FROM events_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["event_name"] == "Research Mixer"


class TestStats:
    def test_stats_counts_zero_initially(self, db: Database) -> None:
        stats = db.get_stats()
        assert stats["total_questions"] == 0
        assert stats["total_initiatives"] == 0
        assert stats["total_feedback"] == 0

    def test_stats_counts_after_inserts(self, db: Database) -> None:
        db.log_question(1, "q1", "t", 80.0, None)
        db.log_question(2, "q2", "t", 75.0, None)
        db.log_feedback(3, "fb", None)
        stats = db.get_stats()
        assert stats["total_questions"] == 2
        assert stats["total_feedback"] == 1

    def test_top_topics_returned(self, db: Database) -> None:
        for _ in range(3):
            db.log_question(1, "q", "about_gsa", 90.0, None)
        db.log_question(2, "q2", "funding", 70.0, None)
        stats = db.get_stats()
        topics = [t["matched_topic"] for t in stats["top_topics"]]
        assert "about_gsa" in topics

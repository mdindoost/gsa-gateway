"""All SQLite database operations for GSA Gateway.

User IDs are SHA-256 hashed before storage; raw IDs are never persisted.
"""

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def hash_user_id(user_id: int | str) -> str:
    """Return the SHA-256 hex digest of a Discord user ID."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()


class Database:
    """Wraps a SQLite connection and provides typed CRUD helpers."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the database connection."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        logger.info("Database connected: %s", self.db_path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called.")
        return self._conn

    # ── Schema ────────────────────────────────────────────────────────────────

    def init_tables(self) -> None:
        """Create all tables if they do not already exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS questions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id_hash  TEXT    NOT NULL,
                question_text TEXT    NOT NULL,
                matched_topic TEXT,
                confidence    REAL,
                timestamp     TEXT    NOT NULL,
                guild_id      TEXT
            );

            CREATE TABLE IF NOT EXISTS initiatives (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id_hash    TEXT    NOT NULL,
                title           TEXT    NOT NULL,
                description     TEXT    NOT NULL,
                category        TEXT    NOT NULL,
                include_contact INTEGER NOT NULL DEFAULT 0,
                contact_info    TEXT,
                status          TEXT    NOT NULL DEFAULT 'pending',
                timestamp       TEXT    NOT NULL,
                guild_id        TEXT
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id_hash TEXT    NOT NULL,
                message      TEXT    NOT NULL,
                timestamp    TEXT    NOT NULL,
                guild_id     TEXT
            );

            CREATE TABLE IF NOT EXISTS events_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name      TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                officer_id_hash TEXT    NOT NULL,
                timestamp       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                officer_id_hash TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                detail          TEXT,
                timestamp       TEXT    NOT NULL
            );
        """)
        self.conn.commit()
        logger.info("Database tables initialised.")

    # ── Write helpers ─────────────────────────────────────────────────────────

    def log_question(
        self,
        user_id: int,
        question: str,
        matched_topic: str | None,
        confidence: float | None,
        guild_id: int | str | None,
    ) -> int:
        """Store a student question. Returns the new row ID."""
        cur = self.conn.execute(
            """INSERT INTO questions
               (user_id_hash, question_text, matched_topic, confidence, timestamp, guild_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                hash_user_id(user_id),
                question,
                matched_topic,
                confidence,
                datetime.now(timezone.utc).isoformat(),
                str(guild_id) if guild_id is not None else None,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def log_initiative(
        self,
        user_id: int,
        title: str,
        description: str,
        category: str,
        include_contact: bool,
        contact_info: str | None,
        guild_id: int | str | None,
    ) -> int:
        """Store a student initiative. Returns the new row ID."""
        cur = self.conn.execute(
            """INSERT INTO initiatives
               (user_id_hash, title, description, category,
                include_contact, contact_info, status, timestamp, guild_id)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                hash_user_id(user_id),
                title,
                description,
                category,
                1 if include_contact else 0,
                contact_info if include_contact else None,
                datetime.now(timezone.utc).isoformat(),
                str(guild_id) if guild_id is not None else None,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def log_feedback(
        self,
        user_id: int,
        message: str,
        guild_id: int | str | None,
    ) -> int:
        """Store student feedback. Returns the new row ID."""
        cur = self.conn.execute(
            """INSERT INTO feedback (user_id_hash, message, timestamp, guild_id)
               VALUES (?, ?, ?, ?)""",
            (
                hash_user_id(user_id),
                message,
                datetime.now(timezone.utc).isoformat(),
                str(guild_id) if guild_id is not None else None,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def log_admin_action(
        self, officer_id: int, action: str, detail: str | None
    ) -> None:
        """Record an admin action for audit purposes."""
        self.conn.execute(
            """INSERT INTO admin_actions (officer_id_hash, action, detail, timestamp)
               VALUES (?, ?, ?, ?)""",
            (
                hash_user_id(officer_id),
                action,
                detail,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def log_event_action(
        self, event_name: str, action: str, officer_id: int
    ) -> None:
        """Log a change to an event (creation, update, deletion)."""
        self.conn.execute(
            """INSERT INTO events_log (event_name, action, officer_id_hash, timestamp)
               VALUES (?, ?, ?, ?)""",
            (
                event_name,
                action,
                hash_user_id(officer_id),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    # ── Read helpers ──────────────────────────────────────────────────────────

    def get_recent_initiatives(self, days: int = 7) -> list[dict[str, Any]]:
        """Return initiatives submitted in the last *days* days."""
        rows = self.conn.execute(
            """SELECT title, description, category, include_contact, status, timestamp
               FROM initiatives
               WHERE timestamp >= datetime('now', ?)
               ORDER BY timestamp DESC""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_feedback(self, days: int = 7) -> list[dict[str, Any]]:
        """Return feedback submitted in the last *days* days."""
        rows = self.conn.execute(
            """SELECT message, timestamp FROM feedback
               WHERE timestamp >= datetime('now', ?)
               ORDER BY timestamp DESC""",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate counts and top search topics."""
        stats: dict[str, Any] = {}

        stats["total_questions"] = self.conn.execute(
            "SELECT COUNT(*) FROM questions"
        ).fetchone()[0]

        stats["total_initiatives"] = self.conn.execute(
            "SELECT COUNT(*) FROM initiatives"
        ).fetchone()[0]

        stats["total_feedback"] = self.conn.execute(
            "SELECT COUNT(*) FROM feedback"
        ).fetchone()[0]

        rows = self.conn.execute(
            """SELECT matched_topic, COUNT(*) AS cnt
               FROM questions
               WHERE matched_topic IS NOT NULL
               GROUP BY matched_topic
               ORDER BY cnt DESC
               LIMIT 5"""
        ).fetchall()
        stats["top_topics"] = [
            {"matched_topic": r["matched_topic"], "count": r["cnt"]} for r in rows
        ]

        return stats

    def get_all_questions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, question_text, matched_topic, confidence, timestamp FROM questions"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_initiatives(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT id, title, description, category, include_contact,
                      contact_info, status, timestamp
               FROM initiatives"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_feedback(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, message, timestamp FROM feedback"
        ).fetchall()
        return [dict(r) for r in rows]

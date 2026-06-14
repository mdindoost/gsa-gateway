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
                guild_id      TEXT,
                was_answered  BOOLEAN DEFAULT FALSE,
                platform      TEXT    DEFAULT 'discord',
                mode          TEXT    DEFAULT 'gsa'
            );

            CREATE TABLE IF NOT EXISTS response_feedback (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id          INTEGER NOT NULL,
                user_id_hash         TEXT    NOT NULL,
                rating               TEXT    NOT NULL,
                detail               TEXT    DEFAULT NULL,
                platform             TEXT    NOT NULL,
                timestamp            TEXT    NOT NULL,
                original_question_id INTEGER DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_stats (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id_hash        TEXT    NOT NULL,
                session_start       TEXT    NOT NULL,
                session_end         TEXT,
                turn_count          INTEGER DEFAULT 0,
                questions_answered  INTEGER DEFAULT 0,
                sources_used        TEXT,
                channel_name        TEXT,
                timestamp           TEXT    DEFAULT CURRENT_TIMESTAMP
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

            CREATE TABLE IF NOT EXISTS events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT    NOT NULL,
                date              TEXT    NOT NULL,
                time              TEXT    NOT NULL DEFAULT 'TBD',
                location          TEXT    NOT NULL DEFAULT 'TBD',
                description       TEXT    NOT NULL DEFAULT '',
                organizer         TEXT    NOT NULL DEFAULT 'GSA',
                rsvp_link         TEXT    NOT NULL DEFAULT '',
                category          TEXT    NOT NULL DEFAULT 'general',
                reminder_sent_7d  INTEGER NOT NULL DEFAULT 0,
                reminder_sent_1d  INTEGER NOT NULL DEFAULT 0,
                reminder_sent_1h  INTEGER NOT NULL DEFAULT 0,
                announcement_sent INTEGER NOT NULL DEFAULT 0,
                channel_posted    TEXT,
                created_at        TEXT    NOT NULL,
                created_by        TEXT    NOT NULL
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
        self.migrate_events_columns()
        logger.info("Database tables initialised.")

    def migrate_events_columns(self) -> None:
        """Add reminder tracking columns to events table if missing (safe for old DBs)."""
        columns = [
            ("reminder_sent_7d",  "INTEGER NOT NULL DEFAULT 0"),
            ("reminder_sent_1d",  "INTEGER NOT NULL DEFAULT 0"),
            ("reminder_sent_1h",  "INTEGER NOT NULL DEFAULT 0"),
            ("announcement_sent", "INTEGER NOT NULL DEFAULT 0"),
            ("channel_posted",    "TEXT"),
        ]
        for col, typedef in columns:
            try:
                self.conn.execute(f"ALTER TABLE events ADD COLUMN {col} {typedef}")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

    def migrate_rag_columns(self) -> None:
        """Add RAG-related columns to questions table if missing."""
        for col, typedef in [
            ("was_answered", "BOOLEAN DEFAULT FALSE"),
            ("platform",     "TEXT DEFAULT 'discord'"),
            ("mode",         "TEXT DEFAULT 'gsa'"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE questions ADD COLUMN {col} {typedef}")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

    # ── Feedback write ────────────────────────────────────────────────────────

    def log_feedback_rating(
        self,
        question_id: int,
        user_id: int | str,
        rating: str,
        platform: str,
        detail: str | None = None,
        original_question_id: int | None = None,
    ) -> int:
        """Record user feedback on a bot response. Returns the new row ID.

        original_question_id: set on retry rows to link back to the question
        that triggered the 🔄 so we can measure whether retry improved outcomes.
        """
        cur = self.conn.execute(
            """INSERT INTO response_feedback
               (question_id, user_id_hash, rating, detail, platform, timestamp,
                original_question_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                question_id,
                hash_user_id(user_id),
                rating,
                detail,
                platform,
                datetime.now(timezone.utc).isoformat(),
                original_question_id,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Write helpers ─────────────────────────────────────────────────────────

    def log_question(
        self,
        user_id: int | str,
        question: str,
        matched_topic: str | None,
        confidence: float | None,
        guild_id: int | str | None,
        platform: str = "discord",
        mode: str = "gsa",
    ) -> int:
        """Store a student question. Returns the new row ID."""
        cur = self.conn.execute(
            """INSERT INTO questions
               (user_id_hash, question_text, matched_topic, confidence, timestamp, guild_id, platform, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hash_user_id(user_id),
                question,
                matched_topic,
                confidence,
                datetime.now(timezone.utc).isoformat(),
                str(guild_id) if guild_id is not None else None,
                platform,
                mode,
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

        rows = self.conn.execute(
            """SELECT mode,
                      COUNT(*)                    AS questions,
                      COUNT(DISTINCT user_id_hash) AS users
               FROM questions
               GROUP BY mode"""
        ).fetchall()
        stats["questions_by_mode"] = {
            r["mode"]: {"questions": r["questions"], "users": r["users"]}
            for r in rows
        }

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

    def get_feedback_stats(self) -> dict[str, Any]:
        """Return counts of each rating type and satisfaction rate."""
        rows = self.conn.execute(
            "SELECT rating, COUNT(*) AS cnt FROM response_feedback GROUP BY rating"
        ).fetchall()
        totals = {r["rating"]: r["cnt"] for r in rows}
        up = totals.get("thumbs_up", 0)
        down = totals.get("thumbs_down", 0)
        total = up + down
        return {
            "thumbs_up": up,
            "thumbs_down": down,
            "regenerate": totals.get("regenerate", 0),
            "satisfaction_rate": round(up / total * 100, 1) if total > 0 else None,
        }

    def get_gaps_summary(self, days: int = 30) -> dict[str, Any]:
        """Return gap analysis data for /admin_gaps.

        A gap question is one where: matched_topic IS NULL, confidence < 60,
        or the response received a thumbs_down.  Priority score formula:
        (times_asked * 2) + (thumbs_down_count * 3) + (1 - avg_confidence/100) * 5
        """
        since = f"-{days} days"

        total_q = self.conn.execute(
            "SELECT COUNT(*) FROM questions WHERE timestamp >= datetime('now', ?)",
            (since,),
        ).fetchone()[0]

        answered = self.conn.execute(
            """SELECT COUNT(*) FROM questions
               WHERE timestamp >= datetime('now', ?)
                 AND matched_topic IS NOT NULL AND confidence >= 60""",
            (since,),
        ).fetchone()[0]

        answered_rate = round(answered / total_q * 100, 1) if total_q > 0 else 0.0

        # Pre-aggregate feedback per question_id to avoid row-count inflation in AVG.
        rows = self.conn.execute(
            """SELECT
                 q.question_text,
                 COUNT(DISTINCT q.id)                        AS times_asked,
                 ROUND(AVG(COALESCE(q.confidence, 0.0)), 1)  AS avg_confidence,
                 COALESCE(SUM(fb.td_count), 0)               AS thumbs_down_count,
                 MAX(q.timestamp)                            AS last_asked
               FROM questions q
               LEFT JOIN (
                   SELECT question_id,
                          SUM(CASE WHEN rating = 'thumbs_down' THEN 1 ELSE 0 END) AS td_count
                   FROM response_feedback
                   GROUP BY question_id
               ) fb ON fb.question_id = q.id
               WHERE q.timestamp >= datetime('now', ?)
                 AND (
                   q.matched_topic IS NULL
                   OR q.confidence < 60
                   OR fb.td_count > 0
                 )
               GROUP BY q.question_text
               LIMIT 50""",
            (since,),
        ).fetchall()

        top_gaps: list[dict[str, Any]] = []
        for r in rows:
            times = r["times_asked"] or 0
            td = r["thumbs_down_count"] or 0
            conf = float(r["avg_confidence"] or 0.0)
            priority = (times * 2) + (td * 3) + (1 - conf / 100) * 5
            top_gaps.append({
                "question_text": r["question_text"],
                "times_asked": times,
                "avg_confidence": conf,
                "thumbs_down_count": td,
                "last_asked": r["last_asked"],
                "priority_score": round(priority, 2),
            })
        top_gaps.sort(key=lambda x: x["priority_score"], reverse=True)
        top_gaps = top_gaps[:20]

        never_matched = self.conn.execute(
            """SELECT DISTINCT question_text FROM questions
               WHERE matched_topic IS NULL
                 AND timestamp >= datetime('now', ?)
               ORDER BY timestamp DESC
               LIMIT 10""",
            (since,),
        ).fetchall()

        return {
            "total_questions": total_q,
            "answered_rate": answered_rate,
            "top_gaps": top_gaps,
            "feedback_totals": self.get_feedback_stats(),
            "never_matched_topics": [r["question_text"] for r in never_matched],
        }

    # ── Events CRUD ───────────────────────────────────────────────────────────

    def add_event(
        self,
        name: str,
        date: str,
        time: str,
        location: str,
        description: str,
        organizer: str,
        rsvp_link: str,
        category: str,
        officer_id: int,
    ) -> int:
        """Insert a new event. Returns the new row ID."""
        cur = self.conn.execute(
            """INSERT INTO events
               (name, date, time, location, description, organizer, rsvp_link,
                category, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, date, time, location, description, organizer, rsvp_link,
                category,
                datetime.now(timezone.utc).isoformat(),
                hash_user_id(officer_id),
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_events_for_reminders(self) -> list[dict[str, Any]]:
        """Return all future (and today's) events for reminder processing."""
        rows = self.conn.execute(
            "SELECT * FROM events WHERE date >= date('now') ORDER BY date ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_upcoming_events_db(self, days: int = 7) -> list[dict[str, Any]]:
        """Return events between today and today+days (inclusive), counting "today"
        by the audience's **US Eastern** day — not UTC. With ``date('now')`` (UTC),
        an event on the current ET day is dropped once UTC rolls past midnight (after
        ~8 PM ET) — the same UTC/local boundary bug fixed for the World Cup digest.
        Event dates are stored as ET calendar dates."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("America/New_York")).date()
        rows = self.conn.execute(
            """SELECT * FROM events
               WHERE date >= ? AND date <= ?
               ORDER BY date ASC""",
            (today.isoformat(), (today + timedelta(days=days)).isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_events(self) -> list[dict[str, Any]]:
        """Return all events ordered by date."""
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY date ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminder_sent(self, event_id: int, reminder_type: str) -> None:
        """Mark a reminder as sent. reminder_type: '7d', '1d', or '1h'."""
        column_map = {
            "7d": "reminder_sent_7d",
            "1d": "reminder_sent_1d",
            "1h": "reminder_sent_1h",
        }
        col = column_map.get(reminder_type)
        if col is None:
            logger.warning("Unknown reminder type: %s", reminder_type)
            return
        self.conn.execute(f"UPDATE events SET {col} = 1 WHERE id = ?", (event_id,))
        self.conn.commit()

    def mark_announcement_sent(self, event_id: int, channel_name: str) -> None:
        """Mark an event's initial announcement as sent."""
        self.conn.execute(
            "UPDATE events SET announcement_sent = 1, channel_posted = ? WHERE id = ?",
            (channel_name, event_id),
        )
        self.conn.commit()

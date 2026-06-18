"""In-memory judge session state machine for the Telegram bot."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from v2.core.judging import db as jdb


@dataclass
class _JudgeSession:
    state: str = "idle"          # idle | awaiting_pin | ready | scoring | confirming
    judge_id: int | None = None
    judge_name: str | None = None
    event_id: int | None = None
    event_name: str | None = None
    criteria: list[str] = field(default_factory=list)
    presenter_number: int | None = None
    presenter_name: str | None = None
    presenter_dept: str | None = None
    collected_scores: list[int] = field(default_factory=list)


_RE_TRIGGER = re.compile(r"judge\s+mode", re.IGNORECASE)
_RE_LOGOUT  = re.compile(r"exit\s+judge\s+mode|logout", re.IGNORECASE)
_RE_NUMBER  = re.compile(r"\b(\d+)\b")
_RE_YES     = re.compile(r"^yes$", re.IGNORECASE)
_RE_REDO    = re.compile(r"^redo$", re.IGNORECASE)


class JudgingSessionManager:
    """Manages per-user Telegram judge sessions during a live judging event.

    Completed scores are written to DB immediately on 'yes'.
    In-progress answers (Q1..Qn) live in memory only — if the bot restarts,
    the judge simply enters the participant number again.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._sessions: dict[str, _JudgeSession] = {}

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _sess(self, user_id: str) -> _JudgeSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = _JudgeSession()
        return self._sessions[user_id]

    def handle(self, user_id: str, text: str) -> tuple[str | None, bool]:
        """Process an incoming Telegram message.

        Returns (response_text, consumed).
        If consumed is False the caller should route to the normal RAG handler.
        """
        sess = self._sess(user_id)
        t = text.strip()

        # ── logout from any active state ──────────────────────────────────
        if _RE_LOGOUT.search(t) and sess.state != "idle":
            self._sessions.pop(user_id, None)
            return "You have exited Judge Mode. I'll answer GSA questions normally now.", True

        # ── IDLE ──────────────────────────────────────────────────────────
        if sess.state == "idle":
            if not _RE_TRIGGER.search(t):
                return None, False  # normal GSA message — pass through

            conn = self._conn()
            try:
                event = jdb.get_open_event(conn)
            finally:
                conn.close()

            if event is None:
                return "No judging event is currently open. Contact the admin.", True

            # Check if this Telegram user is already authenticated
            conn = self._conn()
            try:
                existing = jdb.get_judge_by_telegram_hash(conn, event["id"], user_id)
            finally:
                conn.close()

            if existing:
                sess.state = "ready"
                sess.judge_id = existing["id"]
                sess.judge_name = existing["name"]
                sess.event_id = event["id"]
                sess.event_name = event["name"]
                sess.criteria = event["criteria"]
                return (
                    f"Welcome back, *{existing['name']}*! "
                    f"Judging is open for *{event['name']}*.\n\n"
                    "Say a participant number to continue scoring.\n"
                    "Say *exit judge mode* to return to normal mode."
                ), True

            sess.state = "awaiting_pin"
            sess.event_id = event["id"]
            sess.event_name = event["name"]
            sess.criteria = event["criteria"]
            return f"*Judge Mode* — {event['name']}\n\nPlease enter your judge PIN:", True

        # ── AWAITING_PIN ──────────────────────────────────────────────────
        if sess.state == "awaiting_pin":
            conn = self._conn()
            try:
                judge = jdb.authenticate_judge(conn, sess.event_id, t, user_id)
                if judge is None:
                    return "Invalid PIN or this PIN is already in use. Contact the admin.", True
                conn.commit()
            finally:
                conn.close()

            sess.state = "ready"
            sess.judge_id = judge["id"]
            sess.judge_name = judge["name"]
            return (
                f"Authenticated as *{judge['name']}*.\n\n"
                f"Judging is open for *{sess.event_name}*.\n"
                "Say a participant number to start scoring.\n"
                "Say *exit judge mode* at any time to return to normal mode."
            ), True

        # ── READY ─────────────────────────────────────────────────────────
        if sess.state == "ready":
            m = _RE_NUMBER.search(t)
            if not m:
                return (
                    "Say the participant's number to start scoring (e.g. *104*).\n"
                    "Say *exit judge mode* to exit."
                ), True

            number = int(m.group(1))
            conn = self._conn()
            try:
                presenter = jdb.get_presenter(conn, sess.event_id, number)
                if presenter is None:
                    return f"Participant #{number} not found. Check the number and try again.", True
                already = jdb.has_scored(conn, sess.event_id, sess.judge_id, number)
                if already:
                    return (
                        f"You already submitted a score for Participant #{number}. "
                        "Contact the admin if you need to change it."
                    ), True
            finally:
                conn.close()

            sess.state = "scoring"
            sess.presenter_number = number
            sess.presenter_name = presenter["name"]
            sess.presenter_dept = presenter["department"]
            sess.collected_scores = []

            dept_str = f" ({presenter['department']})" if presenter["department"] else ""
            return (
                f"Scoring *#{number} — {presenter['name']}*{dept_str}\n\n"
                f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* (1–5):"
            ), True

        # ── SCORING ───────────────────────────────────────────────────────
        if sess.state == "scoring":
            if _RE_REDO.match(t):
                sess.collected_scores = []
                return (
                    f"Starting over for *#{sess.presenter_number} — {sess.presenter_name}*\n\n"
                    f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* (1–5):"
                ), True

            try:
                score = int(t)
            except ValueError:
                idx = len(sess.collected_scores) + 1
                return (
                    f"Please enter a number from 1 to 5.\n"
                    f"*Q{idx}/{len(sess.criteria)} — {sess.criteria[idx - 1]}* (1–5):"
                ), True

            if not 1 <= score <= 5:
                idx = len(sess.collected_scores) + 1
                return (
                    "Score must be between 1 and 5.\n"
                    f"*Q{idx}/{len(sess.criteria)} — {sess.criteria[idx - 1]}* (1–5):"
                ), True

            sess.collected_scores.append(score)
            idx = len(sess.collected_scores)

            if idx < len(sess.criteria):
                return (
                    f"*Q{idx + 1}/{len(sess.criteria)} — {sess.criteria[idx]}* (1–5):"
                ), True

            # All criteria answered → show confirmation
            sess.state = "confirming"
            lines = [f"{c}: *{s}*" for c, s in zip(sess.criteria, sess.collected_scores)]
            avg = sum(sess.collected_scores) / len(sess.collected_scores)
            return (
                f"Review your scores for *#{sess.presenter_number} — {sess.presenter_name}*:\n"
                + "\n".join(lines)
                + f"\n\n*Average: {avg:.2f}*\n\n"
                "Type *yes* to submit or *redo* to start over."
            ), True

        # ── CONFIRMING ────────────────────────────────────────────────────
        if sess.state == "confirming":
            if _RE_REDO.match(t):
                sess.state = "scoring"
                sess.collected_scores = []
                return (
                    f"Starting over for *#{sess.presenter_number} — {sess.presenter_name}*\n\n"
                    f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* (1–5):"
                ), True

            if _RE_YES.match(t):
                conn = self._conn()
                try:
                    jdb.submit_score(
                        conn,
                        sess.event_id,
                        sess.judge_id,
                        sess.presenter_number,
                        sess.criteria,
                        sess.collected_scores,
                    )
                    conn.commit()
                finally:
                    conn.close()

                pnum = sess.presenter_number
                sess.state = "ready"
                sess.presenter_number = None
                sess.presenter_name = None
                sess.presenter_dept = None
                sess.collected_scores = []
                return (
                    f"Score submitted for Participant #{pnum}.\n\n"
                    "Say the next participant number to continue."
                ), True

            return "Type *yes* to submit or *redo* to start over.", True

        return None, False

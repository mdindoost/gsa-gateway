"""In-memory judge and presenter session state machine for the Telegram bot."""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field

from v2.core.judging import db as jdb


@dataclass
class _JudgeSession:
    state: str = "idle"
    # idle | awaiting_pin | ready | scoring | confirming
    # | presenter_awaiting_number
    # | audience_ready | audience_confirming
    judge_id: int | None = None
    judge_name: str | None = None
    event_id: int | None = None
    event_name: str | None = None
    criteria: list[str] = field(default_factory=list)
    score_min: int = 1
    score_max: int = 5
    presenter_number: int | None = None
    presenter_name: str | None = None
    presenter_dept: str | None = None
    collected_scores: list[int] = field(default_factory=list)
    # audience mode
    pending_vote_number: int | None = None
    pending_vote_name: str | None = None
    pending_vote_dept: str | None = None
    pre_audience_state: str | None = None  # state to restore after voting
    # H3: PIN brute-force protection
    pin_attempts: int = 0
    pin_locked_until: float = 0.0  # epoch seconds


_RE_JUDGE_TRIGGER     = re.compile(r"judge\s+mode", re.IGNORECASE)
_RE_PRESENTER_TRIGGER = re.compile(r"presenter\s+mode", re.IGNORECASE)
_RE_AUDIENCE_TRIGGER  = re.compile(r"audience\s+mode", re.IGNORECASE)
_RE_LOGOUT            = re.compile(r"exit\s+(judge|presenter|audience)\s+mode|logout", re.IGNORECASE)
_RE_MY_SCORES         = re.compile(r"my\s+scores?", re.IGNORECASE)
_RE_NUMBER            = re.compile(r"\b(\d+)\b")
_RE_PURE_NUMBER       = re.compile(r"^\d+$")   # M5: bare number only, not "104 yes"
_RE_YES               = re.compile(r"^yes$", re.IGNORECASE)
_RE_REDO              = re.compile(r"^redo$", re.IGNORECASE)


def _score_range_str(mn: int, mx: int) -> str:
    return f"{mn}–{mx}"


class JudgingSessionManager:
    """Per-user Telegram session state for judging and presenter registration.

    Completed scores are written to DB immediately on 'yes'.
    In-progress answers live in memory only — a bot restart means the judge
    re-enters the participant number.
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

    # ── public entry point ──────────────────────────────────────────────────

    def handle(self, user_id: str, text: str) -> tuple[str | None, bool]:
        """Process an incoming Telegram message.

        Returns (response_text, consumed).
        If consumed is False the caller routes to the normal RAG handler.
        """
        sess = self._sess(user_id)
        t = text.strip()

        # logout from any active state
        if _RE_LOGOUT.search(t) and sess.state != "idle":
            # H1: warn if mid-scoring so judge knows their in-progress answers are lost
            if sess.state in ("scoring", "confirming"):
                self._sessions.pop(user_id, None)
                return (
                    f"Your in-progress scores for *#{sess.presenter_number} — "
                    f"{sess.presenter_name}* were NOT saved.\n\n"
                    "You have exited judge mode."
                ), True
            self._sessions.pop(user_id, None)
            return "You have exited. I'll answer GSA questions normally now.", True

        # ── IDLE ──────────────────────────────────────────────────────────
        if sess.state == "idle":
            if _RE_PRESENTER_TRIGGER.search(t):
                return self._start_presenter_mode(user_id, sess)
            if _RE_JUDGE_TRIGGER.search(t):
                return self._start_judge_mode(user_id, sess)
            if _RE_AUDIENCE_TRIGGER.search(t):
                return self._start_audience_mode(user_id, sess)
            return None, False  # normal GSA message — pass to RAG

        # ── PRESENTER_AWAITING_NUMBER ──────────────────────────────────────
        if sess.state == "presenter_awaiting_number":
            return self._handle_presenter_number(user_id, sess, t)

        # ── AWAITING_PIN ──────────────────────────────────────────────────
        if sess.state == "awaiting_pin":
            return self._handle_pin(user_id, sess, t)

        # ── READY (judge) ─────────────────────────────────────────────────
        if sess.state == "ready":
            if _RE_AUDIENCE_TRIGGER.search(t):
                return self._start_audience_mode(user_id, sess)
            if _RE_MY_SCORES.search(t):
                return self._show_my_scores(sess)
            return self._handle_ready(sess, t)

        # ── SCORING ───────────────────────────────────────────────────────
        if sess.state == "scoring":
            return self._handle_scoring(sess, t)

        # ── CONFIRMING ────────────────────────────────────────────────────
        if sess.state == "confirming":
            return self._handle_confirming(sess, t)

        # ── AUDIENCE_READY ────────────────────────────────────────────────
        if sess.state == "audience_ready":
            return self._handle_audience_ready(user_id, sess, t)

        # ── AUDIENCE_CONFIRMING ───────────────────────────────────────────
        if sess.state == "audience_confirming":
            return self._handle_audience_confirming(user_id, sess, t)

        return None, False

    # ── IDLE helpers ────────────────────────────────────────────────────────

    def _start_judge_mode(self, user_id: str, sess: _JudgeSession) -> tuple[str, bool]:
        conn = self._conn()
        try:
            event = jdb.get_open_event(conn)
            if event is None:
                any_event = jdb.get_any_event(conn)
                if any_event and any_event["status"] == "setup":
                    return (
                        f"Judging for *{any_event['name']}* has not opened yet. "
                        "Please check back later."
                    ), True
                if any_event and any_event["status"] == "closed":
                    return (
                        f"Judging for *{any_event['name']}* is now closed. Thank you!"
                    ), True
                return "No judging event is currently open. Contact the admin.", True

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
            sess.score_min = event.get("score_min", 1)
            sess.score_max = event.get("score_max", 5)
            return (
                f"Welcome back, *{existing['name']}*! "
                f"Judging is open for *{event['name']}*.\n\n"
                "Say a participant number to continue scoring.\n"
                "Say *my scores* to see what you've scored so far.\n"
                "Say *exit judge mode* to return to normal mode."
            ), True

        sess.state = "awaiting_pin"
        sess.event_id = event["id"]
        sess.event_name = event["name"]
        sess.criteria = event["criteria"]
        sess.score_min = event.get("score_min", 1)
        sess.score_max = event.get("score_max", 5)
        return f"*Judge Mode* — {event['name']}\n\nPlease enter your judge PIN:", True

    def _start_presenter_mode(self, user_id: str, sess: _JudgeSession) -> tuple[str, bool]:
        conn = self._conn()
        try:
            event = jdb.get_open_event(conn)
            if event is None:
                any_event = jdb.get_any_event(conn)
                if any_event and any_event["status"] == "setup":
                    return (
                        f"Registration for *{any_event['name']}* has not opened yet."
                    ), True
                if any_event and any_event["status"] == "closed":
                    return (
                        f"*{any_event['name']}* is now closed."
                    ), True
                return "No event is currently open. Contact the admin.", True
        finally:
            conn.close()

        sess.state = "presenter_awaiting_number"
        sess.event_id = event["id"]
        sess.event_name = event["name"]
        return (
            f"*Presenter Mode* — {event['name']}\n\n"
            "Please enter your participant number:"
        ), True

    # ── PRESENTER_AWAITING_NUMBER ────────────────────────────────────────────

    def _handle_presenter_number(self, user_id: str, sess: _JudgeSession,
                                  t: str) -> tuple[str, bool]:
        m = _RE_NUMBER.search(t)
        if not m:
            return "Please enter your participant number (e.g. *104*).", True

        number = int(m.group(1))
        conn = self._conn()
        try:
            presenter = jdb.get_presenter(conn, sess.event_id, number)
            if presenter is None:
                return (
                    f"Participant #{number} not found. "
                    "Check your number and try again, or contact Mohammad."
                ), True
            ok = jdb.register_presenter(conn, sess.event_id, number, user_id)
            if not ok:
                return (
                    f"Participant #{number} is already registered to a different account. "
                    "If this is a mistake, contact Mohammad immediately."
                ), True
            conn.commit()
        finally:
            conn.close()

        dept_str = f", {presenter['department']}" if presenter["department"] else ""
        self._sessions.pop(user_id, None)  # done — return to idle
        return (
            f"You are registered as *#{number} — {presenter['name']}*{dept_str}.\n\n"
            "If there's a mistake, contact Mohammad immediately."
        ), True

    # ── AWAITING_PIN ────────────────────────────────────────────────────────

    def _handle_pin(self, user_id: str, sess: _JudgeSession, t: str) -> tuple[str, bool]:
        # H3: enforce lockout before attempting any DB check
        now = time.time()
        if sess.pin_locked_until > now:
            remaining = int((sess.pin_locked_until - now) / 60) + 1
            return (
                f"Too many failed attempts. Try again in {remaining} minute(s)."
            ), True

        conn = self._conn()
        try:
            judge = jdb.authenticate_judge(conn, sess.event_id, t, user_id)
            if judge is None:
                sess.pin_attempts += 1
                if sess.pin_attempts >= 5:
                    sess.pin_locked_until = now + 600  # 10-minute lockout
                    sess.pin_attempts = 0
                    return (
                        "Too many failed attempts. "
                        "Please wait 10 minutes before trying again."
                    ), True
                return "Invalid PIN or this PIN is already in use. Contact the admin.", True
            conn.commit()
        finally:
            conn.close()

        sess.pin_attempts = 0
        sess.state = "ready"
        sess.judge_id = judge["id"]
        sess.judge_name = judge["name"]
        rng = _score_range_str(sess.score_min, sess.score_max)
        return (
            f"Authenticated as *{judge['name']}*.\n\n"
            f"Judging is open for *{sess.event_name}*.\n"
            f"Scores range: {rng}\n"
            "Say a participant number to start scoring.\n"
            "Say *my scores* to review what you've scored so far.\n"
            "Say *exit judge mode* at any time to return to normal mode."
        ), True

    # ── READY ────────────────────────────────────────────────────────────────

    def _handle_ready(self, sess: _JudgeSession, t: str) -> tuple[str, bool]:
        m = _RE_NUMBER.search(t)
        if not m:
            return (
                "Say the participant's number to start scoring (e.g. *104*).\n"
                "Say *my scores* to see your scoring history.\n"
                "Say *exit judge mode* to exit."
            ), True

        number = int(m.group(1))
        conn = self._conn()
        try:
            presenter = jdb.get_presenter(conn, sess.event_id, number)
            if presenter is None:
                return f"Participant #{number} not found. Check the number and try again.", True
            existing_score = jdb.get_score(conn, sess.event_id, sess.judge_id, number)
        finally:
            conn.close()

        if existing_score:
            lines = [f"{c}: *{v}*" for c, v in existing_score["scores"].items()]
            total = sum(existing_score["scores"].values())
            denom = len(sess.criteria) * sess.score_max
            return (
                f"You already scored *#{number} — {presenter['name']}*:\n"
                + "\n".join(lines)
                + f"\n\n*Total: {total}/{denom}*\n\n"
                "Contact the admin if you need a correction.\n"
                "Say another participant number to continue."
            ), True

        sess.state = "scoring"
        sess.presenter_number = number
        sess.presenter_name = presenter["name"]
        sess.presenter_dept = presenter["department"]
        sess.collected_scores = []

        dept_str = f" ({presenter['department']})" if presenter["department"] else ""
        rng = _score_range_str(sess.score_min, sess.score_max)
        return (
            f"Scoring *#{number} — {presenter['name']}*{dept_str}\n\n"
            f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* ({rng}):"
        ), True

    def _show_my_scores(self, sess: _JudgeSession) -> tuple[str, bool]:
        conn = self._conn()
        try:
            scored = jdb.get_all_scores_by_judge(conn, sess.event_id, sess.judge_id)
        finally:
            conn.close()

        if not scored:
            return "You haven't scored anyone yet for this event.", True

        denom = len(sess.criteria) * sess.score_max
        lines = [f"*Scores so far ({len(scored)} presenter(s)):*"]
        for item in scored:
            total = sum(item["scores"].values())
            lines.append(f"  #{item['number']} {item['name']} — Total: {total}/{denom}")
        lines.append("\nSay a participant number to continue scoring.")
        return "\n".join(lines), True

    # ── SCORING ──────────────────────────────────────────────────────────────

    def _handle_scoring(self, sess: _JudgeSession, t: str) -> tuple[str, bool]:
        rng = _score_range_str(sess.score_min, sess.score_max)

        if _RE_REDO.match(t):
            sess.collected_scores = []
            return (
                f"Starting over for *#{sess.presenter_number} — {sess.presenter_name}*\n\n"
                f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* ({rng}):"
            ), True

        try:
            score = int(t)
        except ValueError:
            idx = len(sess.collected_scores) + 1
            return (
                f"Please enter a number from {rng}.\n"
                f"*Q{idx}/{len(sess.criteria)} — {sess.criteria[idx - 1]}* ({rng}):"
            ), True

        if not sess.score_min <= score <= sess.score_max:
            idx = len(sess.collected_scores) + 1
            return (
                f"Score must be between {rng}.\n"
                f"*Q{idx}/{len(sess.criteria)} — {sess.criteria[idx - 1]}* ({rng}):"
            ), True

        sess.collected_scores.append(score)
        idx = len(sess.collected_scores)

        if idx < len(sess.criteria):
            return (
                f"*Q{idx + 1}/{len(sess.criteria)} — {sess.criteria[idx]}* ({rng}):"
            ), True

        # All criteria answered → confirmation
        sess.state = "confirming"
        lines = [f"{c}: *{s}*" for c, s in zip(sess.criteria, sess.collected_scores)]
        total = sum(sess.collected_scores)
        denom = len(sess.criteria) * sess.score_max
        return (
            f"Review your scores for *#{sess.presenter_number} — {sess.presenter_name}*:\n"
            + "\n".join(lines)
            + f"\n\n*Total: {total}/{denom}*\n\n"
            "Type *yes* to submit or *redo* to start over."
        ), True

    # ── AUDIENCE MODE ─────────────────────────────────────────────────────────

    def _start_audience_mode(self, user_id: str, sess: _JudgeSession) -> tuple[str, bool]:
        conn = self._conn()
        try:
            event = jdb.get_open_event(conn)
            if event is None:
                any_event = jdb.get_any_event(conn)
                if any_event and any_event["status"] == "setup":
                    return (
                        f"Audience voting for *{any_event['name']}* has not opened yet."
                    ), True
                if any_event and any_event["status"] == "closed":
                    return f"*{any_event['name']}* is now closed.", True
                return "No event is currently open.", True

            if event.get("audience_voting") != "open":
                return (
                    f"Audience voting for *{event['name']}* is not active yet. "
                    "Check back later."
                ), True

            existing_vote = jdb.get_vote(conn, event["id"], user_id)
        finally:
            conn.close()

        pre = sess.state  # "idle" or "ready"
        sess.pre_audience_state = pre
        sess.state = "audience_ready"
        sess.event_id = event["id"]
        sess.event_name = event["name"]

        if existing_vote:
            dept_str = f" ({existing_vote['department']})" if existing_vote["department"] else ""
            return (
                f"*Audience Mode* — {event['name']}\n\n"
                f"You previously voted for *#{existing_vote['presenter_number']} — "
                f"{existing_vote['name']}*{dept_str}.\n\n"
                "Say a presenter number to change your vote, or *exit audience mode* to go back."
            ), True

        return (
            f"*Audience Mode* — {event['name']}\n\n"
            "Say a presenter number to cast your vote:"
        ), True

    def _handle_audience_ready(self, user_id: str, sess: _JudgeSession,
                                t: str) -> tuple[str, bool]:
        m = _RE_NUMBER.search(t)
        if not m:
            return (
                "Say a presenter number to vote (e.g. *104*).\n"
                "Say *exit audience mode* to go back."
            ), True

        number = int(m.group(1))
        conn = self._conn()
        try:
            presenter = jdb.get_presenter(conn, sess.event_id, number)
        finally:
            conn.close()

        if presenter is None:
            return f"Participant #{number} not found. Check the number and try again.", True

        sess.pending_vote_number = number
        sess.pending_vote_name = presenter["name"]
        sess.pending_vote_dept = presenter["department"]
        sess.state = "audience_confirming"

        dept_str = f" ({presenter['department']})" if presenter["department"] else ""
        return (
            f"You are voting for *#{number} — {presenter['name']}*{dept_str}.\n\n"
            "Type *yes* to confirm or say a different number."
        ), True

    def _handle_audience_confirming(self, user_id: str, sess: _JudgeSession,
                                     t: str) -> tuple[str, bool]:
        # M5: only treat as a number change if the message is a bare digit string,
        # so "104 yes" doesn't shadow the "yes" confirmation path
        if _RE_PURE_NUMBER.match(t):
            number = int(t)
            conn = self._conn()
            try:
                presenter = jdb.get_presenter(conn, sess.event_id, number)
            finally:
                conn.close()
            if presenter is None:
                return f"Participant #{number} not found. Try another number.", True
            sess.pending_vote_number = number
            sess.pending_vote_name = presenter["name"]
            sess.pending_vote_dept = presenter["department"]
            dept_str = f" ({presenter['department']})" if presenter["department"] else ""
            return (
                f"You are voting for *#{number} — {presenter['name']}*{dept_str}.\n\n"
                "Type *yes* to confirm or say a different number."
            ), True

        if _RE_YES.match(t):
            conn = self._conn()
            try:
                # H-new-1: re-check audience voting is still open at cast time
                ev = jdb.get_event(conn, sess.event_id)
                if ev is None or ev.get("audience_voting") != "open":
                    self._sessions.pop(user_id, None)
                    return (
                        f"Audience voting for *{sess.event_name}* has closed. "
                        "Your vote was NOT recorded."
                    ), True
                jdb.cast_vote(conn, sess.event_id, user_id, sess.pending_vote_number)
                conn.commit()
            except Exception:
                # M4: DB write failed — inform user, stay in confirming so they can retry
                return (
                    "Something went wrong recording your vote. "
                    "Please type *yes* again to retry."
                ), True
            finally:
                conn.close()

            name = sess.pending_vote_name
            pnum = sess.pending_vote_number
            return self._restore_from_audience(user_id, sess, pnum, name)

        return "Type *yes* to confirm or say a different presenter number.", True

    def _restore_from_audience(self, user_id: str, sess: _JudgeSession,
                                pnum: int, name: str) -> tuple[str, bool]:
        """After a vote is cast, return the user to judge mode or idle."""
        pre = sess.pre_audience_state
        sess.pending_vote_number = None
        sess.pending_vote_name = None
        sess.pending_vote_dept = None
        sess.pre_audience_state = None

        if pre == "ready" and sess.judge_id is not None:
            sess.state = "ready"
            return (
                f"Vote cast for *#{pnum} — {name}*!\n\n"
                "You're back in *Judge Mode*. Say a participant number to continue scoring."
            ), True

        # non-judge or came from idle — return to idle
        self._sessions.pop(user_id, None)
        return (
            f"Vote cast for *#{pnum} — {name}*!\n\n"
            "Say *audience mode* again if you want to change your vote."
        ), True

    # ── CONFIRMING ───────────────────────────────────────────────────────────

    def _handle_confirming(self, sess: _JudgeSession, t: str) -> tuple[str, bool]:
        if _RE_REDO.match(t):
            sess.state = "scoring"
            sess.collected_scores = []
            rng = _score_range_str(sess.score_min, sess.score_max)
            return (
                f"Starting over for *#{sess.presenter_number} — {sess.presenter_name}*\n\n"
                f"*Q1/{len(sess.criteria)} — {sess.criteria[0]}* ({rng}):"
            ), True

        if _RE_YES.match(t):
            conn = self._conn()
            try:
                # H-new-2: re-check event is still open at submit time
                ev = jdb.get_open_event(conn)
                if ev is None or ev["id"] != sess.event_id:
                    sess.state = "ready"
                    sess.presenter_number = None
                    sess.presenter_name = None
                    sess.presenter_dept = None
                    sess.collected_scores = []
                    return (
                        f"Judging for *{sess.event_name}* is now closed. "
                        "Your scores were NOT saved."
                    ), True
                jdb.submit_score(
                    conn,
                    sess.event_id,
                    sess.judge_id,
                    sess.presenter_number,
                    sess.criteria,
                    sess.collected_scores,
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # C2: duplicate submit (double-tap / retry) — score already saved
                pnum = sess.presenter_number
                sess.state = "ready"
                sess.presenter_number = None
                sess.presenter_name = None
                sess.presenter_dept = None
                sess.collected_scores = []
                return (
                    f"Your scores for Participant #{pnum} were already saved "
                    "(looks like a duplicate send). "
                    "Contact the admin if you need a correction."
                ), True
            except Exception:
                # M4: DB write failed — don't leave judge stuck in confirming
                return (
                    "Something went wrong saving your scores. "
                    "Please type *yes* again or contact the admin."
                ), True
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
                "Say the next participant number to continue, or *my scores* to review."
            ), True

        return "Type *yes* to submit or *redo* to start over.", True

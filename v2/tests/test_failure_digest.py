"""Active failure digest (accuracy backlog #3).

Spec: docs/superpowers/specs/2026-06-22-active-failure-digest-design.md
"""
import asyncio
import datetime
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.integration.failure_digest import (
    FailureDigestSource, build_digest_body, build_failure_digest_draft, collect_failures,
)

NOW = datetime.datetime(2026, 6, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _ts(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat()


def _db():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE questions(id INTEGER PRIMARY KEY, user_id_hash TEXT, question_text TEXT,
            matched_topic TEXT, confidence REAL, timestamp TEXT, guild_id TEXT, was_answered INT,
            platform TEXT, mode TEXT, org_id INT);
        CREATE TABLE response_feedback(id INTEGER PRIMARY KEY, question_id INT, user_id_hash TEXT,
            rating TEXT, detail TEXT, platform TEXT, timestamp TEXT, original_question_id INT, org_id INT);
        """
    )
    return c


def _q(c, qid, text, conf, hours_ago):
    c.execute("INSERT INTO questions(id,question_text,confidence,timestamp) VALUES(?,?,?,?)",
              (qid, text, conf, _ts(hours_ago)))


def _fb(c, qid, rating, detail, hours_ago):
    c.execute("INSERT INTO response_feedback(question_id,rating,detail,timestamp) VALUES(?,?,?,?)",
              (qid, rating, detail, _ts(hours_ago)))


SINCE = (NOW - datetime.timedelta(days=1)).isoformat()


def test_collect_window_and_signals():
    c = _db()
    _q(c, 1, "what is his position", 40, 2)          # low-conf, in window
    _q(c, 2, "who is the GSA president", 90, 2)       # answered well, in window
    _q(c, 3, "old failing question", 30, 40)          # low-conf but OUTSIDE 24h window
    _fb(c, 1, "thumbs_down", "off_topic", 1)          # 👎 in window
    _fb(c, 2, "thumbs_up", None, 1)
    data = collect_failures(c, SINCE, top_n=10)
    assert data["total"] == 2                          # q3 excluded by window
    assert data["down"] == 1 and data["up"] == 1
    assert [r[0] for r in data["thumbs_down"]] == ["what is his position"]
    assert "what is his position" in [r[0] for r in data["low_conf"]]
    assert "old failing question" not in [r[0] for r in data["low_conf"]]


def test_null_confidence_not_low_conf():
    c = _db()
    _q(c, 1, "unscored question", None, 1)             # NULL confidence → NOT low-conf [R5]
    data = collect_failures(c, SINCE, top_n=10)
    assert data["low_conf"] == []


def test_boundary_minus_one_second_excluded():
    c = _db()
    # a row 1 second older than the boundary must be excluded [R1 — ISO-T boundary]
    boundary = NOW - datetime.timedelta(days=1)
    older = (boundary - datetime.timedelta(seconds=1)).isoformat()
    c.execute("INSERT INTO questions(id,question_text,confidence,timestamp) VALUES(1,'edge',10,?)", (older,))
    _fb(c, 1, "thumbs_down", "x", 0)  # feedback recent, but the question is old; low-conf keys off question ts
    data = collect_failures(c, boundary.isoformat(), top_n=10)
    assert data["low_conf"] == [] and data["total"] == 0


def test_build_body_contains_signals():
    data = {"total": 5, "up": 1, "down": 2, "regen": 1,
            "thumbs_down": [("what is his position", "off_topic", 40.0)],
            "low_conf": [("dining hall hours", 3, 22.0)]}
    body = build_digest_body(data)
    assert "what is his position" in body and "off_topic" in body
    assert "dining hall hours" in body
    assert "vanity" in body.lower()                    # the answer-rate caveat


def test_quiet_window_no_draft():
    c = _db()
    _q(c, 1, "who is the GSA president", 90, 1)         # answered well, no 👎, no low-conf
    draft = build_failure_digest_draft(1, c, SINCE, NOW.date(),
                                       discord_channel="gsa-ops", scheduled_for=None)
    assert draft is None                               # quiet → no post


def test_draft_dedup_key_and_type():
    c = _db()
    _q(c, 1, "what is his position", 40, 1)
    _fb(c, 1, "thumbs_down", "off_topic", 1)
    draft = build_failure_digest_draft(1, c, SINCE, NOW.date(),
                                       discord_channel="gsa-ops", scheduled_for="2026-06-22 13:00:00")
    assert draft is not None
    assert draft.type == "digest"
    assert draft.dedup_key == "failure-digest-2026-06-22"
    assert draft.discord_channel == "gsa-ops"
    assert draft.scheduled_for == "2026-06-22 13:00:00"


def test_poll_returns_list():
    c = _db()
    # seed a recent 👎 relative to REAL now so the source's real-clock window includes it
    now = datetime.datetime.now(datetime.timezone.utc)
    c.execute("INSERT INTO questions(id,question_text,confidence,timestamp) VALUES(1,'what is his position',40,?)",
              (now.isoformat(),))
    c.execute("INSERT INTO response_feedback(question_id,rating,detail,timestamp) VALUES(1,'thumbs_down','off_topic',?)",
              (now.isoformat(),))
    src = FailureDigestSource(c, org_id=1, discord_channel="gsa-ops", post_hour_et=9)
    drafts = asyncio.run(src.poll())
    assert len(drafts) == 1 and drafts[0].type == "digest"
    assert drafts[0].scheduled_for is not None         # HOUR_ET via morning_utc [R2]

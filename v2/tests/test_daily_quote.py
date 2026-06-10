"""Tests for the DailyQuoteSource example generator."""
from __future__ import annotations

import asyncio
import datetime
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import enqueue_post
from v2.integration.daily_quote import (
    QUOTES, DailyQuoteSource, build_quote_draft, quote_for,
)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    c.commit()
    return c


def test_quote_for_is_deterministic_and_in_range():
    day = datetime.date(2026, 6, 10)
    q1 = quote_for(day)
    q2 = quote_for(day)
    assert q1 == q2
    assert q1 in QUOTES
    # rotates across days
    assert quote_for(day) != quote_for(day + datetime.timedelta(days=1))


def test_build_quote_draft_fields():
    day = datetime.date(2026, 6, 10)
    draft = build_quote_draft(org_id=2, day=day, channels=["discord"])
    q = quote_for(day)
    assert draft.org_id == 2
    assert draft.type == "broadcast"
    assert draft.source_type == "daily_quote"
    assert draft.channels == ["discord"]
    assert draft.dedup_key == day.isoformat()
    assert q["text"] in draft.content
    assert q["author"] in draft.content
    assert draft.metadata["author"] == q["author"]
    assert draft.metadata["date"] == day.isoformat()


def test_build_quote_draft_default_channels():
    draft = build_quote_draft(org_id=2, day=datetime.date(2026, 6, 10))
    assert draft.channels == ["discord", "telegram"]


def test_source_poll_returns_one_draft():
    drafts = asyncio.run(DailyQuoteSource(org_id=2, channels=["discord"]).poll())
    assert len(drafts) == 1
    assert drafts[0].source_type == "daily_quote"


def test_enqueue_inserts_scheduled_row(conn):
    draft = build_quote_draft(org_id=2, day=datetime.date(2026, 6, 10), channels=["discord"])
    pid = enqueue_post(conn, draft)
    row = conn.execute("SELECT status, type, source_type FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["source_type"] == "daily_quote"


def test_same_day_dedup_posts_once(conn):
    day = datetime.date(2026, 6, 10)
    first = enqueue_post(conn, build_quote_draft(org_id=2, day=day, channels=["discord"]))
    second = enqueue_post(conn, build_quote_draft(org_id=2, day=day, channels=["discord"]))
    assert first == second
    n = conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='daily_quote'").fetchone()[0]
    assert n == 1

"""Tests for the DailyFixturesSource World Cup schedule generator."""
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
from v2.integration.daily_fixtures import (
    DailyFixturesSource, build_fixtures_draft, format_fixtures, _kickoff_et,
)

DAY = datetime.date(2026, 6, 11)
M_MEX = {"homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "South Africa"},
         "utcDate": "2026-06-11T19:00:00Z", "group": "GROUP_A", "stage": "GROUP_STAGE"}
M_EARLY = {"homeTeam": {"name": "Spain"}, "awayTeam": {"name": "Brazil"},
           "utcDate": "2026-06-11T16:00:00Z", "group": "GROUP_B", "stage": "GROUP_STAGE"}


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'GSA','gsa','gsa')")
    c.commit()
    return c


def test_kickoff_et_converts_utc_to_eastern():
    # 19:00 UTC on Jun 11 (EDT, UTC-4) -> 3:00 PM ET
    assert _kickoff_et("2026-06-11T19:00:00Z") == "3:00 PM ET"


def test_kickoff_et_bad_input():
    assert _kickoff_et("not-a-date") == "TBD"


def test_format_orders_by_kickoff_and_labels():
    text = format_fixtures(DAY, [M_MEX, M_EARLY])  # given out of order
    assert "World Cup fixtures" in text
    assert "June" in text and "11" in text
    assert "Mexico" in text and "Group A" in text
    assert "3:00 PM ET" in text
    # earlier kickoff (Spain 16:00) must come before later (Mexico 19:00)
    assert text.index("Spain") < text.index("Mexico")


def test_build_draft_fields():
    draft = build_fixtures_draft(org_id=2, day=DAY, matches=[M_MEX], channels=["discord"])
    assert draft is not None
    assert draft.type == "broadcast"
    assert draft.source_type == "wc_fixtures"
    assert draft.dedup_key == DAY.isoformat()
    assert draft.channels == ["discord"]
    assert draft.metadata["match_count"] == 1
    assert draft.metadata["date"] == DAY.isoformat()


def test_build_draft_default_channels():
    draft = build_fixtures_draft(org_id=2, day=DAY, matches=[M_MEX])
    assert draft.channels == ["discord", "telegram"]


def test_build_draft_no_matches_returns_none():
    assert build_fixtures_draft(org_id=2, day=DAY, matches=[]) is None


def test_source_poll_with_matches(monkeypatch):
    async def fake_fetch(api_key, day):
        return [M_MEX, M_EARLY]
    monkeypatch.setattr("v2.integration.daily_fixtures.fetch_fixtures", fake_fetch)
    drafts = asyncio.run(DailyFixturesSource("k", org_id=2).poll())
    assert len(drafts) == 1
    assert drafts[0].source_type == "wc_fixtures"


def test_source_poll_no_matches_posts_nothing(monkeypatch):
    async def fake_fetch(api_key, day):
        return []
    monkeypatch.setattr("v2.integration.daily_fixtures.fetch_fixtures", fake_fetch)
    drafts = asyncio.run(DailyFixturesSource("k", org_id=2).poll())
    assert drafts == []


def test_enqueue_and_same_day_dedup(conn):
    draft = build_fixtures_draft(org_id=2, day=DAY, matches=[M_MEX], channels=["discord"])
    first = enqueue_post(conn, draft)
    row = conn.execute("SELECT status, source_type FROM posts WHERE id=?", (first,)).fetchone()
    assert row["status"] == "scheduled"
    assert row["source_type"] == "wc_fixtures"
    # re-enqueue same day -> dedup to one row
    second = enqueue_post(conn, build_fixtures_draft(org_id=2, day=DAY, matches=[M_MEX], channels=["discord"]))
    assert first == second
    n = conn.execute("SELECT COUNT(*) FROM posts WHERE source_type='wc_fixtures'").fetchone()[0]
    assert n == 1

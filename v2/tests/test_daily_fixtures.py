"""Tests for the DailyFixturesSource World Cup schedule generator."""
from __future__ import annotations

import asyncio
import datetime
import logging
import sys
from pathlib import Path

import aiohttp
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.publishing.sources import enqueue_post
from v2.integration.daily_fixtures import (
    DailyFixturesSource, build_fixtures_draft, fetch_fixtures, format_fixtures,
    morning_utc, _fixture_line, _kickoff_et,
)

DAY = datetime.date(2026, 6, 11)
# real Jun 11 fixtures (so venues resolve and the FIFA audit stays clean)
M_MEX = {"homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "South Africa"},
         "utcDate": "2026-06-11T19:00:00Z", "group": "GROUP_A", "stage": "GROUP_STAGE"}
M_KOR = {"homeTeam": {"name": "South Korea"}, "awayTeam": {"name": "Czechia"},
         "utcDate": "2026-06-12T02:00:00Z", "group": "GROUP_A", "stage": "GROUP_STAGE"}


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
    assert _kickoff_et("") == "TBD"
    assert _kickoff_et(None) == "TBD"


def test_fixture_line_tbd_teams():
    # knockout slot before teams qualify: API sends name=None / placeholder
    m = {"homeTeam": None, "awayTeam": {"name": ""},
         "utcDate": "2026-07-10T20:00:00Z", "group": None, "stage": "QUARTER_FINALS"}
    line = _fixture_line(m)
    assert "TBD vs" in line and "vs ⚽ TBD" in line
    assert "Quarter-finals" in line  # clean stage label, not "Quarter Finals"


def test_fetch_fixtures_degrades_to_empty_on_error(monkeypatch):
    class _RaisingSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, *a, **k):
            raise aiohttp.ClientError("boom")
    monkeypatch.setattr("v2.integration.daily_fixtures.aiohttp.ClientSession",
                        lambda *a, **k: _RaisingSession())
    out = asyncio.run(fetch_fixtures("k", DAY))
    assert out == []


def test_format_orders_by_kickoff_and_labels():
    text = format_fixtures(DAY, [M_KOR, M_MEX])  # given out of kickoff order
    assert "World Cup fixtures" in text
    assert "June" in text and "11" in text
    assert "Mexico" in text and "Group A" in text
    assert "3:00 PM ET" in text
    # earlier kickoff (Mexico 19:00Z) must come before later (Korea 02:00Z next day)
    assert text.index("Mexico") < text.index("South Korea")


def test_fixture_line_includes_venue():
    # venue comes from the FIFA schedule, not the API
    assert "📍 Mexico City" in _fixture_line(M_MEX)


def test_api_team_names_have_flags():
    # API spellings that previously fell back to ⚽
    from v2.integration.worldcup_tracker import flag
    for name in ("United States", "Cape Verde Islands", "Congo DR", "Curaçao", "Haiti"):
        assert flag(name) != "⚽", name


def test_fixture_line_is_three_line_block():
    # teams / time·group / venue on separate lines (Telegram-friendly)
    block = _fixture_line(M_MEX).split("\n")
    assert len(block) == 3
    assert "Mexico" in block[0] and "South Africa" in block[0] and "—" not in block[0]
    assert block[1] == "3:00 PM ET · Group A"
    assert block[2] == "📍 Mexico City"


def test_audit_logs_discrepancy_for_unknown_fixture(caplog):
    bogus = {"homeTeam": {"name": "Spain"}, "awayTeam": {"name": "Brazil"},  # never meet in groups
             "utcDate": "2026-06-11T19:00:00Z", "group": "GROUP_B", "stage": "GROUP_STAGE"}
    with caplog.at_level(logging.WARNING, logger="v2.integration.daily_fixtures"):
        build_fixtures_draft(org_id=2, day=DAY, matches=[bogus])
    assert any("WC schedule audit" in r.message for r in caplog.records)


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


def test_morning_utc_converts_et_hour():
    # 9:00 AM ET (EDT, UTC-4) on Jun 11 -> 13:00 UTC
    assert morning_utc(DAY, 9) == "2026-06-11 13:00:00"


def test_poll_schedules_for_morning(monkeypatch):
    async def fake_fetch(api_key, day):
        return [M_MEX]
    monkeypatch.setattr("v2.integration.daily_fixtures.fetch_fixtures", fake_fetch)
    drafts = asyncio.run(DailyFixturesSource("k", org_id=2, post_hour_et=9).poll())
    today = datetime.date.today()
    assert drafts[0].scheduled_for == morning_utc(today, 9)


def test_source_poll_with_matches(monkeypatch):
    async def fake_fetch(api_key, day):
        return [M_MEX, M_KOR]
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

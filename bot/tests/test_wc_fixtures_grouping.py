"""Regression test for the WC daily-digest date grouping.

Bug: the digest grouped fixtures by US-Eastern date, but FIFA dates by venue-local
day. A late Pacific kickoff (9 PM PT in Vancouver = midnight ET) was filed under
the next day and dropped from its FIFA day's digest.

Real example (from the live API, 2026-06-13): Australia v Turkey has
utcDate=2026-06-14T04:00:00Z (midnight ET June 14), but FIFA lists it June 13.
"""

import datetime

from v2.integration.daily_fixtures import matches_for_day
from v2.integration.wc_schedule import fifa_date


def _m(home, away, utc):
    return {"homeTeam": {"name": home}, "awayTeam": {"name": away}, "utcDate": utc}


# The exact 8-match window the API returns for dateFrom=2026-06-13&dateTo=2026-06-14.
API_WINDOW = [
    _m("United States", "Paraguay", "2026-06-13T01:00:00Z"),   # FIFA June 12
    _m("Qatar", "Switzerland", "2026-06-13T19:00:00Z"),        # FIFA June 13
    _m("Brazil", "Morocco", "2026-06-13T22:00:00Z"),           # FIFA June 13
    _m("Haiti", "Scotland", "2026-06-14T01:00:00Z"),           # FIFA June 13 (9 PM ET)
    _m("Australia", "Turkey", "2026-06-14T04:00:00Z"),         # FIFA June 13 (9 PM PT) <-- bug
    _m("Germany", "Curaçao", "2026-06-14T17:00:00Z"),          # FIFA June 14
    _m("Netherlands", "Japan", "2026-06-14T20:00:00Z"),        # FIFA June 14
    _m("Ivory Coast", "Ecuador", "2026-06-14T23:00:00Z"),      # FIFA June 14
]


def _pairs(matches):
    return {(m["homeTeam"]["name"], m["awayTeam"]["name"]) for m in matches}


def test_fifa_date_resolves_pairing_including_api_alias():
    assert fifa_date("Australia", "Türkiye") == "2026-06-13"
    assert fifa_date("Australia", "Turkey") == "2026-06-13"   # football-data spelling


def test_late_pacific_game_is_grouped_on_its_fifa_day():
    day13 = matches_for_day(API_WINDOW, datetime.date(2026, 6, 13))
    assert ("Australia", "Turkey") in _pairs(day13)   # the fix
    assert _pairs(day13) == {
        ("Qatar", "Switzerland"), ("Brazil", "Morocco"),
        ("Haiti", "Scotland"), ("Australia", "Turkey"),
    }


def test_late_pacific_game_not_double_listed_next_day():
    day14 = matches_for_day(API_WINDOW, datetime.date(2026, 6, 14))
    assert ("Australia", "Turkey") not in _pairs(day14)
    # genuine June-14 games still group on the 14th
    assert ("Germany", "Curaçao") in _pairs(day14)


def test_unknown_pairing_falls_back_to_eastern_date():
    # A pairing not in the FIFA group-stage index (e.g. a knockout TBD) groups by ET.
    m = _m("Winner Group A", "Runner-up Group B", "2026-07-04T23:00:00Z")  # 7 PM ET
    assert matches_for_day([m], datetime.date(2026, 7, 4)) == [m]
    assert matches_for_day([m], datetime.date(2026, 7, 5)) == []

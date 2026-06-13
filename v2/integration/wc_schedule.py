"""Authoritative FIFA World Cup 2026 schedule — venue reference + API auditor.

The football-data.org API gives us live fixtures/results but NOT venues. This
module holds the official FIFA schedule (source: FIFA's published match schedule,
v17) so we can:

  1. **Fill the gap** — attach a host city to each fixture (``venue_for``).
  2. **Audit the API** — ``reconcile`` matches an API fixture to the FIFA record
     by date + teams (naming normalised) and reports any discrepancy (unknown
     team name, date mismatch, missing match) for investigation. FIFA is treated
     as ground truth; the API remains the live source.

Join key for the group stage is (date, {both teams}) — order-independent and
accent/alias-normalised. Knockout matches use placeholders until teams qualify,
so they are keyed by FIFA match number / date and not team-joined here.
"""
from __future__ import annotations

import datetime
import logging
import unicodedata
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# FIFA schedules by US Eastern date ("All times are Eastern Time (ET)"); the API
# dates fixtures by UTC, so an evening US kickoff lands on the NEXT UTC day. We
# always match on the ET-local date to reconcile the two.
_ET = ZoneInfo("America/New_York")


def et_date(when: str) -> str:
    """ET-local date (YYYY-MM-DD) for an API utcDate timestamp. Accepts a bare
    date (returned as-is) so callers may pass either."""
    if not when:
        return ""
    if "T" not in when:
        return when[:10]
    try:
        dt = datetime.datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(_ET)
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return when[:10]

# ── Group stage: (date, home, away, group, venue) — names exactly as FIFA lists ──
GROUP_STAGE: list[tuple[str, str, str, str, str]] = [
    ("2026-06-11", "Mexico", "South Africa", "A", "Mexico City Stadium"),
    ("2026-06-11", "Korea Republic", "Czechia", "A", "Estadio Guadalajara"),
    ("2026-06-12", "Canada", "Bosnia and Herzegovina", "B", "Toronto Stadium"),
    ("2026-06-12", "USA", "Paraguay", "D", "Los Angeles Stadium"),
    ("2026-06-13", "Haiti", "Scotland", "C", "Boston Stadium"),
    ("2026-06-13", "Australia", "Türkiye", "D", "BC Place Vancouver"),
    ("2026-06-13", "Brazil", "Morocco", "C", "New York New Jersey Stadium"),
    ("2026-06-13", "Qatar", "Switzerland", "B", "San Francisco Bay Area Stadium"),
    ("2026-06-14", "Côte d'Ivoire", "Ecuador", "E", "Philadelphia Stadium"),
    ("2026-06-14", "Germany", "Curaçao", "E", "Houston Stadium"),
    ("2026-06-14", "Netherlands", "Japan", "F", "Dallas Stadium"),
    ("2026-06-14", "Sweden", "Tunisia", "F", "Estadio Monterrey"),
    ("2026-06-15", "Saudi Arabia", "Uruguay", "H", "Miami Stadium"),
    ("2026-06-15", "Spain", "Cabo Verde", "H", "Atlanta Stadium"),
    ("2026-06-15", "IR Iran", "New Zealand", "G", "Los Angeles Stadium"),
    ("2026-06-15", "Belgium", "Egypt", "G", "Seattle Stadium"),
    ("2026-06-16", "France", "Senegal", "I", "New York New Jersey Stadium"),
    ("2026-06-16", "Iraq", "Norway", "I", "Boston Stadium"),
    ("2026-06-16", "Argentina", "Algeria", "J", "Kansas City Stadium"),
    ("2026-06-16", "Austria", "Jordan", "J", "San Francisco Bay Area Stadium"),
    ("2026-06-17", "Ghana", "Panama", "L", "Toronto Stadium"),
    ("2026-06-17", "England", "Croatia", "L", "Dallas Stadium"),
    ("2026-06-17", "Portugal", "Congo DR", "K", "Houston Stadium"),
    ("2026-06-17", "Uzbekistan", "Colombia", "K", "Mexico City Stadium"),
    ("2026-06-18", "Czechia", "South Africa", "A", "Atlanta Stadium"),
    ("2026-06-18", "Switzerland", "Bosnia and Herzegovina", "B", "Los Angeles Stadium"),
    ("2026-06-18", "Canada", "Qatar", "B", "BC Place Vancouver"),
    ("2026-06-18", "Mexico", "Korea Republic", "A", "Estadio Guadalajara"),
    ("2026-06-19", "Brazil", "Haiti", "C", "Philadelphia Stadium"),
    ("2026-06-19", "Scotland", "Morocco", "C", "Boston Stadium"),
    ("2026-06-19", "Türkiye", "Paraguay", "D", "San Francisco Bay Area Stadium"),
    ("2026-06-19", "USA", "Australia", "D", "Seattle Stadium"),
    ("2026-06-20", "Germany", "Côte d'Ivoire", "E", "Toronto Stadium"),
    ("2026-06-20", "Ecuador", "Curaçao", "E", "Kansas City Stadium"),
    ("2026-06-20", "Netherlands", "Sweden", "F", "Houston Stadium"),
    ("2026-06-20", "Tunisia", "Japan", "F", "Estadio Monterrey"),
    ("2026-06-21", "Uruguay", "Cabo Verde", "H", "Miami Stadium"),
    ("2026-06-21", "Spain", "Saudi Arabia", "H", "Atlanta Stadium"),
    ("2026-06-21", "Belgium", "IR Iran", "G", "Los Angeles Stadium"),
    ("2026-06-21", "New Zealand", "Egypt", "G", "BC Place Vancouver"),
    ("2026-06-22", "Norway", "Senegal", "I", "New York New Jersey Stadium"),
    ("2026-06-22", "France", "Iraq", "I", "Philadelphia Stadium"),
    ("2026-06-22", "Argentina", "Austria", "J", "Dallas Stadium"),
    ("2026-06-22", "Jordan", "Algeria", "J", "San Francisco Bay Area Stadium"),
    ("2026-06-23", "England", "Ghana", "L", "Boston Stadium"),
    ("2026-06-23", "Panama", "Croatia", "L", "Toronto Stadium"),
    ("2026-06-23", "Portugal", "Uzbekistan", "K", "Houston Stadium"),
    ("2026-06-23", "Colombia", "Congo DR", "K", "Estadio Guadalajara"),
    ("2026-06-24", "Scotland", "Brazil", "C", "Miami Stadium"),
    ("2026-06-24", "Morocco", "Haiti", "C", "Atlanta Stadium"),
    ("2026-06-24", "Switzerland", "Canada", "B", "BC Place Vancouver"),
    ("2026-06-24", "Bosnia and Herzegovina", "Qatar", "B", "Seattle Stadium"),
    ("2026-06-24", "Czechia", "Mexico", "A", "Mexico City Stadium"),
    ("2026-06-24", "South Africa", "Korea Republic", "A", "Estadio Monterrey"),
    ("2026-06-25", "Curaçao", "Côte d'Ivoire", "E", "Philadelphia Stadium"),
    ("2026-06-25", "Ecuador", "Germany", "E", "New York New Jersey Stadium"),
    ("2026-06-25", "Japan", "Sweden", "F", "Dallas Stadium"),
    ("2026-06-25", "Tunisia", "Netherlands", "F", "Kansas City Stadium"),
    ("2026-06-25", "Türkiye", "USA", "D", "Los Angeles Stadium"),
    ("2026-06-25", "Paraguay", "Australia", "D", "San Francisco Bay Area Stadium"),
    ("2026-06-26", "Norway", "France", "I", "Boston Stadium"),
    ("2026-06-26", "Senegal", "Iraq", "I", "Toronto Stadium"),
    ("2026-06-26", "Egypt", "IR Iran", "G", "Seattle Stadium"),
    ("2026-06-26", "New Zealand", "Belgium", "G", "BC Place Vancouver"),
    ("2026-06-26", "Cabo Verde", "Saudi Arabia", "H", "Houston Stadium"),
    ("2026-06-26", "Uruguay", "Spain", "H", "Estadio Guadalajara"),
    ("2026-06-27", "Panama", "England", "L", "New York New Jersey Stadium"),
    ("2026-06-27", "Croatia", "Ghana", "L", "Philadelphia Stadium"),
    ("2026-06-27", "Algeria", "Austria", "J", "Kansas City Stadium"),
    ("2026-06-27", "Jordan", "Argentina", "J", "Dallas Stadium"),
    ("2026-06-27", "Colombia", "Portugal", "K", "Miami Stadium"),
    ("2026-06-27", "Congo DR", "Uzbekistan", "K", "Atlanta Stadium"),
]

# ── Knockout: (date, match_no, description, venue) — teams TBD until qualified ──
KNOCKOUT: list[tuple[str, int, str, str]] = [
    ("2026-06-28", 73, "Group A runners-up v Group B runners-up", "Los Angeles Stadium"),
    ("2026-06-29", 74, "Group E winners v Group A/B/C/D/F third place", "Boston Stadium"),
    ("2026-06-29", 75, "Group F winners v Group C runners-up", "Estadio Monterrey"),
    ("2026-06-29", 76, "Group C winners v Group F runners-up", "Houston Stadium"),
    ("2026-06-30", 77, "Group I winners v Group C/D/F/G/H third place", "New York New Jersey Stadium"),
    ("2026-06-30", 78, "Group E runners-up v Group I runners-up", "Dallas Stadium"),
    ("2026-06-30", 79, "Group A winners v Group C/E/F/H/I third place", "Mexico City Stadium"),
    ("2026-07-01", 80, "Group L winners v Group E/H/I/J/K third place", "Atlanta Stadium"),
    ("2026-07-01", 81, "Group D winners v Group B/E/F/I/J third place", "San Francisco Bay Area Stadium"),
    ("2026-07-01", 82, "Group G winners v Group A/E/H/I/J third place", "Seattle Stadium"),
    ("2026-07-02", 83, "Group K runners-up v Group L runners-up", "Toronto Stadium"),
    ("2026-07-02", 84, "Group H winners v Group J runners-up", "Los Angeles Stadium"),
    ("2026-07-02", 85, "Group B winners v Group E/F/G/I/J third place", "BC Place Vancouver"),
    ("2026-07-03", 86, "Group J winners v Group H runners-up", "Miami Stadium"),
    ("2026-07-03", 87, "Group K winners v Group D/E/I/J/L third place", "Kansas City Stadium"),
    ("2026-07-03", 88, "Group D runners-up v Group G runners-up", "Dallas Stadium"),
    ("2026-07-04", 89, "Winner match 74 v Winner match 77", "Philadelphia Stadium"),
    ("2026-07-04", 90, "Winner match 73 v Winner match 75", "Houston Stadium"),
    ("2026-07-05", 91, "Winner match 76 v Winner match 78", "New York New Jersey Stadium"),
    ("2026-07-05", 92, "Winner match 79 v Winner match 80", "Mexico City Stadium"),
    ("2026-07-06", 93, "Winner match 83 v Winner match 84", "Dallas Stadium"),
    ("2026-07-06", 94, "Winner match 81 v Winner match 82", "Seattle Stadium"),
    ("2026-07-07", 95, "Winner match 86 v Winner match 88", "Atlanta Stadium"),
    ("2026-07-07", 96, "Winner match 85 v Winner match 87", "BC Place Vancouver"),
    ("2026-07-09", 97, "Winner match 89 v Winner match 90", "Boston Stadium"),
    ("2026-07-10", 98, "Winner match 93 v Winner match 94", "Los Angeles Stadium"),
    ("2026-07-11", 99, "Winner match 91 v Winner match 92", "Miami Stadium"),
    ("2026-07-11", 100, "Winner match 95 v Winner match 96", "Kansas City Stadium"),
    ("2026-07-14", 101, "Winner match 97 v Winner match 98", "Dallas Stadium"),
    ("2026-07-15", 102, "Winner match 99 v Winner match 100", "Atlanta Stadium"),
    ("2026-07-18", 103, "Runner-up match 101 v Runner-up match 102", "Miami Stadium"),
    ("2026-07-19", 104, "Winner match 101 v Winner match 102", "New York New Jersey Stadium"),
]

# Official venue string -> short host-city label for posts.
VENUE_CITY = {
    "Mexico City Stadium": "Mexico City", "Estadio Guadalajara": "Guadalajara",
    "Estadio Monterrey": "Monterrey", "BC Place Vancouver": "Vancouver",
    "Seattle Stadium": "Seattle", "San Francisco Bay Area Stadium": "San Francisco Bay Area",
    "Los Angeles Stadium": "Los Angeles", "Houston Stadium": "Houston",
    "Dallas Stadium": "Dallas", "Kansas City Stadium": "Kansas City",
    "Atlanta Stadium": "Atlanta", "Miami Stadium": "Miami", "Toronto Stadium": "Toronto",
    "Boston Stadium": "Boston", "Philadelphia Stadium": "Philadelphia",
    "New York New Jersey Stadium": "New York/New Jersey",
}

# Variant team spellings -> one canonical key (covers FIFA + football-data names).
# Compared after lowercasing + accent-stripping.
_TEAM_ALIASES = {
    "korea republic": "south korea", "republic of korea": "south korea",
    "ir iran": "iran", "turkiye": "turkey", "cote d'ivoire": "ivory coast",
    # only explicit DR-Congo variants — NOT bare "congo" (Congo-Brazzaville is a
    # distinct nation; safe to collapse only because it isn't in WC 2026)
    "congo dr": "dr congo", "democratic republic of congo": "dr congo",
    "cabo verde": "cape verde", "czech republic": "czechia",
    "bosnia-herzegovina": "bosnia and herzegovina",
    "bosnia & herzegovina": "bosnia and herzegovina",
    "united states": "usa", "united states of america": "usa",
    "cape verde islands": "cape verde",
}


def normalize_team(name: str) -> str:
    """Canonical, accent-free, alias-resolved team key for matching."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().strip().lower()
    return _TEAM_ALIASES.get(s, s)


def city_for(venue: str) -> str:
    """Short host-city label for a venue string (falls back to the venue)."""
    return VENUE_CITY.get(venue, venue)


# frozenset{normalized teams} -> record, built once at import. A group-stage
# pairing is unique (each pair meets exactly once), so the teams alone identify
# the match — robust to the UTC/ET/local-venue date quirks described above.
_TEAM_INDEX: dict[frozenset, dict] = {
    frozenset((normalize_team(home), normalize_team(away))): {
        "date": date, "home": home, "away": away, "group": group,
        "venue": venue, "city": city_for(venue),
    }
    for (date, home, away, group, venue) in GROUP_STAGE
}


def venue_for(home: str, away: str) -> str | None:
    """Host-city label for a group-stage fixture by its (unique) team pairing,
    or None if the pairing isn't in the FIFA group-stage schedule."""
    rec = _TEAM_INDEX.get(frozenset((normalize_team(home), normalize_team(away))))
    return rec["city"] if rec else None


def fifa_date(home: str, away: str) -> str | None:
    """The authoritative FIFA date (YYYY-MM-DD) for a group-stage pairing, or None.

    FIFA dates matches by the **venue-local** day, so this is the day a US (and
    FIFA) audience sees the match on — unlike the API's UTC date, which rolls a
    late west-coast kickoff (9 PM PT = midnight ET) into the next day. The digest
    groups by this so such games land on the right day. None for pairings not in
    the group-stage index (e.g. knockouts) — callers fall back to the ET date."""
    rec = _TEAM_INDEX.get(frozenset((normalize_team(home), normalize_team(away))))
    return rec["date"] if rec else None


def reconcile(when: str, home: str, away: str) -> tuple[str | None, list[str]]:
    """Audit one API group-stage fixture against the FIFA schedule.

    Venue is resolved by the unique team pairing; the date is then sanity-checked.
    Returns (city, discrepancies):
      - pairing found, ET date within 1 day of FIFA -> (city, [])  # exact / TZ artifact
      - pairing found, date off by >1 day           -> (city, [DATE MISMATCH ...])
      - pairing not found                           -> (None, [NO FIFA MATCH ...])
    A ≤1-day gap is expected and benign: FIFA dates by local venue day, the API
    by UTC, so late US-evening / west-coast kickoffs legitimately differ a day.

    KNOWN LIMITATION: because the gap tolerance is 1 day, a *genuine* one-day
    fixture reschedule is intentionally not flagged (indistinguishable from the
    TZ artifact). The venue is keyed by pairing, not date, so it stays correct
    regardless; only a multi-day move is surfaced.
    """
    rec = _TEAM_INDEX.get(frozenset((normalize_team(home), normalize_team(away))))
    if rec is None:
        return None, [
            f"NO FIFA MATCH: {home} v {away} not in the FIFA group-stage schedule "
            f"(check team-name aliasing or a fixture change)"
        ]
    api_d = et_date(when)
    try:
        delta = abs((datetime.date.fromisoformat(api_d)
                     - datetime.date.fromisoformat(rec["date"])).days)
    except (ValueError, TypeError):
        delta = 0
    if delta > 1:
        return rec["city"], [
            f"DATE MISMATCH: API has {home} v {away} on ET date {api_d}, "
            f"FIFA schedule has it on {rec['date']} ({rec['city']})"
        ]
    return rec["city"], []

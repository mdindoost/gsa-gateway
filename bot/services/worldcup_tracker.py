import logging
import json
import datetime
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path


STATE_FILE = Path(__file__).parent.parent / "data" / "worldcup_state.json"

MATCH_STATUS = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "IN_PLAY": "live",
    "PAUSED": "halftime",
    "FINISHED": "finished",
    "POSTPONED": "postponed",
    "CANCELLED": "cancelled",
}

FLAG_MAP = {
    "Brazil": "🇧🇷", "Argentina": "🇦🇷", "France": "🇫🇷",
    "Germany": "🇩🇪", "Spain": "🇪🇸", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "USA": "🇺🇸",
    "Mexico": "🇲🇽", "Japan": "🇯🇵", "South Korea": "🇰🇷",
    "Morocco": "🇲🇦", "Senegal": "🇸🇳", "Iran": "🇮🇷",
    "Saudi Arabia": "🇸🇦", "Australia": "🇦🇺", "Canada": "🇨🇦",
    "Croatia": "🇭🇷", "Serbia": "🇷🇸", "Switzerland": "🇨🇭",
    "Belgium": "🇧🇪", "Uruguay": "🇺🇾", "Colombia": "🇨🇴",
    "Ecuador": "🇪🇨", "Peru": "🇵🇪", "Chile": "🇨🇱",
    "Nigeria": "🇳🇬", "Ghana": "🇬🇭", "Cameroon": "🇨🇲",
    "Italy": "🇮🇹", "Poland": "🇵🇱", "Denmark": "🇩🇰",
    "Austria": "🇦🇹", "Turkey": "🇹🇷", "Ukraine": "🇺🇦",
    "Qatar": "🇶🇦", "Costa Rica": "🇨🇷", "Panama": "🇵🇦",
    "Honduras": "🇭🇳", "Jamaica": "🇯🇲", "Venezuela": "🇻🇪",
    "Bolivia": "🇧🇴", "Paraguay": "🇵🇾", "Algeria": "🇩🇿",
    "Tunisia": "🇹🇳", "Egypt": "🇪🇬", "Mali": "🇲🇱",
    "Ivory Coast": "🇨🇮", "South Africa": "🇿🇦", "Indonesia": "🇮🇩",
    "Thailand": "🇹🇭", "Vietnam": "🇻🇳", "Iraq": "🇮🇶",
    "United Arab Emirates": "🇦🇪", "New Zealand": "🇳🇿",
    "El Salvador": "🇸🇻", "Cuba": "🇨🇺",
    "Trinidad and Tobago": "🇹🇹", "Bahrain": "🇧🇭",
    "Jordan": "🇯🇴", "Palestine": "🇵🇸", "Uzbekistan": "🇺🇿",
    "New Caledonia": "🇳🇨", "Czechia": "🇨🇿",
    "Bosnia-Herzegovina": "🇧🇦", "Slovakia": "🇸🇰",
    "Slovenia": "🇸🇮", "Albania": "🇦🇱", "Georgia": "🇬🇪",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
    "Romania": "🇷🇴", "Hungary": "🇭🇺",
    "Czech Republic": "🇨🇿", "North Macedonia": "🇲🇰",
    "Iceland": "🇮🇸", "Finland": "🇫🇮", "Norway": "🇳🇴",
    "Sweden": "🇸🇪", "Greece": "🇬🇷", "Cape Verde": "🇨🇻",
    "Angola": "🇦🇴", "Tanzania": "🇹🇿", "Zambia": "🇿🇲",
    "Guinea": "🇬🇳", "Mozambique": "🇲🇿",
}


@dataclass
class MatchState:
    match_id: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    status: str
    minute: int
    goals_announced: list
    kickoff_announced: bool
    halftime_announced: bool
    second_half_announced: bool
    fulltime_announced: bool
    stage: str
    group: str
    utc_date: str


class WorldCupTracker:
    def __init__(self, client):
        self.client = client
        self.states: dict[int, MatchState] = {}
        self.logger = logging.getLogger(__name__)
        self.load_state()

    def load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            for match_id_str, fields in raw.items():
                match_id = int(match_id_str)
                self.states[match_id] = MatchState(
                    match_id=fields.get("match_id", match_id),
                    home_team=fields.get("home_team", ""),
                    away_team=fields.get("away_team", ""),
                    home_score=fields.get("home_score", 0),
                    away_score=fields.get("away_score", 0),
                    status=fields.get("status", "scheduled"),
                    minute=fields.get("minute", 0),
                    goals_announced=fields.get("goals_announced", []),
                    kickoff_announced=fields.get("kickoff_announced", False),
                    halftime_announced=fields.get("halftime_announced", False),
                    second_half_announced=fields.get("second_half_announced", False),
                    fulltime_announced=fields.get("fulltime_announced", False),
                    stage=fields.get("stage", ""),
                    group=fields.get("group", ""),
                    utc_date=fields.get("utc_date", ""),
                )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.warning("Could not load worldcup state file: %s", e)
            self.states = {}

    def save_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            str(match_id): dataclasses.asdict(state)
            for match_id, state in self.states.items()
        }
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    async def check_matches(self) -> list[dict]:
        matches = await self.client.get_todays_matches()
        events = []

        for match in matches:
            match_id = match["id"]
            raw_status = match.get("status", "")
            status = MATCH_STATUS.get(raw_status, raw_status.lower())

            home_score = (match["score"]["fullTime"]["home"] or 0)
            away_score = (match["score"]["fullTime"]["away"] or 0)
            minute = match.get("minute") or 0

            prev = self.states.get(match_id)

            # KICKOFF: raw_status is IN_PLAY and prev was scheduled or missing
            if raw_status == "IN_PLAY" and (
                prev is None or prev.status == "scheduled"
            ) and (prev is None or not prev.kickoff_announced):
                events.append({"type": "kickoff", "match": match})
                kickoff_announced = True
            else:
                kickoff_announced = prev.kickoff_announced if prev else False

            # GOAL DETECTION via score diff
            prev_home = prev.home_score if prev else 0
            prev_away = prev.away_score if prev else 0

            if home_score > prev_home or away_score > prev_away:
                full_match = await self.client.get_match(match_id)
                all_goals = (full_match or {}).get("goals", [])
                announced_goals = list(prev.goals_announced) if prev else []

                if all_goals:
                    # Premium tier: use detailed goals data
                    for goal in all_goals:
                        goal_key = {
                            "minute": goal.get("minute"),
                            "scorer": goal.get("scorer", {}).get("name", ""),
                            "team": goal.get("team", {}).get("name", ""),
                        }
                        if goal_key not in announced_goals:
                            events.append({
                                "type": "goal",
                                "match": match,
                                "scorer": goal.get("scorer", {}).get("name", ""),
                                "team": goal.get("team", {}).get("name", ""),
                                "minute": goal.get("minute"),
                            })
                            announced_goals.append(goal_key)
                else:
                    # Free tier: no goals data — fire one event per score increment
                    home_delta = home_score - prev_home
                    away_delta = away_score - prev_away
                    new_score_key = {"score": f"{home_score}-{away_score}"}
                    if new_score_key not in announced_goals:
                        for _ in range(home_delta):
                            events.append({
                                "type": "goal",
                                "match": match,
                                "scoring_team": match["homeTeam"],
                            })
                        for _ in range(away_delta):
                            events.append({
                                "type": "goal",
                                "match": match,
                                "scoring_team": match["awayTeam"],
                            })
                        announced_goals.append(new_score_key)
            else:
                announced_goals = list(prev.goals_announced) if prev else []

            # HALFTIME: PAUSED and prev was IN_PLAY
            if raw_status == "PAUSED" and prev and prev.status == "live" and not prev.halftime_announced:
                events.append({"type": "halftime", "match": match})
                halftime_announced = True
            else:
                halftime_announced = prev.halftime_announced if prev else False

            # SECOND HALF: IN_PLAY and prev was halftime
            if raw_status == "IN_PLAY" and prev and prev.status == "halftime" and not prev.second_half_announced:
                events.append({"type": "second_half", "match": match})
                second_half_announced = True
            else:
                second_half_announced = prev.second_half_announced if prev else False

            # FULLTIME: FINISHED and prev was live or halftime
            if raw_status == "FINISHED" and prev and prev.status in ("live", "halftime") and not prev.fulltime_announced:
                full_match = await self.client.get_match(match_id)
                full_score = (full_match or {}).get("score", {}) if full_match else {}
                events.append({"type": "fulltime", "match": match, "full_score": full_score})
                fulltime_announced = True
            else:
                fulltime_announced = prev.fulltime_announced if prev else False

            # Update state
            self.states[match_id] = MatchState(
                match_id=match_id,
                home_team=match["homeTeam"]["name"],
                away_team=match["awayTeam"]["name"],
                home_score=home_score,
                away_score=away_score,
                status=status,
                minute=minute,
                goals_announced=announced_goals,
                kickoff_announced=kickoff_announced,
                halftime_announced=halftime_announced,
                second_half_announced=second_half_announced,
                fulltime_announced=fulltime_announced,
                stage=match.get("stage", ""),
                group=match.get("group", "") or "",
                utc_date=match.get("utcDate", ""),
            )

        self.save_state()
        return events

    def format_team_name(self, team: dict) -> str:
        name = team.get("name", "") or team.get("shortName", "")
        flag = FLAG_MAP.get(name, "⚽")
        return f"{flag} {name}"

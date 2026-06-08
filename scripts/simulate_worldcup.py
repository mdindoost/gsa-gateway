"""
Simulate the full World Cup notification flow without a live match.

Prints every Discord embed and Telegram message that would be sent,
walking through: kickoff → goal → halftime → second half → goal → fulltime

Usage:
    python scripts/simulate_worldcup.py
    python scripts/simulate_worldcup.py --penalties   # simulate AET + penalties
"""
import sys
import argparse
import datetime
import zoneinfo

sys.path.insert(0, ".")

from bot.services.worldcup_tracker import WorldCupTracker, FLAG_MAP
from bot.services.worldcup_embeds import (
    build_kickoff_embed,
    build_goal_embed,
    build_halftime_embed,
    build_second_half_embed,
    build_fulltime_embed,
    build_daily_schedule_embed,
    format_stage,
    format_group,
    format_score,
    kickoff_to_et,
)

ET = zoneinfo.ZoneInfo("America/New_York")

MEXICO = {"id": 764, "name": "Mexico", "shortName": "Mexico", "tla": "MEX", "crest": ""}
SOUTH_AFRICA = {"id": 327, "name": "South Africa", "shortName": "S.Africa", "tla": "RSA", "crest": ""}

REFEREE = [{"id": 1, "name": "Daniel Siebert", "type": "REFEREE", "nationality": "Germany"}]


def make_match(status, home_score, away_score, half_home=0, half_away=0, duration="REGULAR"):
    return {
        "id": 537327,
        "utcDate": "2026-06-11T19:00:00Z",
        "status": status,
        "matchday": 1,
        "stage": "GROUP_STAGE",
        "group": "GROUP_A",
        "homeTeam": MEXICO,
        "awayTeam": SOUTH_AFRICA,
        "referees": REFEREE,
        "score": {
            "winner": None,
            "duration": duration,
            "fullTime": {"home": home_score, "away": away_score},
            "halfTime": {"home": half_home, "away": half_away},
        },
    }


class FakeTracker:
    def format_team_name(self, team):
        name = team.get("name", "")
        flag = FLAG_MAP.get(name, "⚽")
        return f"{flag} {name}"


def divider(label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print("=" * 60)


def print_embed(embed):
    title = embed.title or ""
    desc  = (embed.description or "").replace("#", "").strip()
    print(f"\n  [{title}]")
    if desc:
        for line in desc.splitlines():
            print(f"    {line.strip()}")
    for f in embed.fields:
        print(f"    {f.name}: {f.value}")
    print(f"    footer: {embed.footer.text if embed.footer else ''}")


def tg_goal(match, scoring_team, tracker):
    home  = tracker.format_team_name(match["homeTeam"])
    away  = tracker.format_team_name(match["awayTeam"])
    score = format_score(match)
    stage = format_stage(match.get("stage", ""))
    group = format_group(match.get("group", "") or "")
    matchday = match.get("matchday")
    goal_line = tracker.format_team_name(scoring_team)

    parts = [f"🏆 {stage}"]
    if group and group != "Knockout Stage":
        parts.append(group)
    if matchday:
        parts.append(f"Matchday {matchday}")
    ctx = " · ".join(parts)

    return (
        f"⚽ GOOOOOAL!\n\n"
        f"{home}  {score}  {away}\n\n"
        f"⚽ {goal_line}\n\n"
        f"{ctx}\n\n"
        f"FIFA World Cup 2026 · GSA Gateway"
    )


def run_simulation(penalties=False):
    tracker = FakeTracker()

    print("\n🧪 World Cup notification simulation")
    print(f"   Match: Mexico vs South Africa")
    print(f"   Date:  Thu Jun 11, 3:00 PM ET")
    print(f"   Mode:  {'Penalty shootout' if penalties else 'Normal (1-0 win)'}")

    # ── 8 AM daily schedule ────────────────────────────────────────────────────
    divider("8:00 AM ET — Daily Schedule (Discord + Telegram)")
    schedule_match = make_match("TIMED", 0, 0)
    embed = build_daily_schedule_embed([schedule_match], tracker)
    print_embed(embed)
    print("\n  [Telegram]")
    print(f"    ⚽ Today's World Cup Matches")
    print(f"    🇲🇽 Mexico vs 🇿🇦 South Africa")
    print(f"    ⏰ 3:00 PM ET")

    # ── Kickoff ────────────────────────────────────────────────────────────────
    divider("3:00 PM ET — Kickoff (within 60s of API status → IN_PLAY)")
    match = make_match("IN_PLAY", 0, 0)
    embed = build_kickoff_embed(match, tracker)
    print_embed(embed)
    print("\n  [Telegram]")
    print("    ⚽ MATCH STARTING NOW!")
    print("    🇲🇽 Mexico  vs  🇿🇦 South Africa")
    print("    🏆 Group Stage · Group A · Matchday 1")
    print("    👨‍⚖️ Daniel Siebert (Germany)")

    # ── Goal (39') ─────────────────────────────────────────────────────────────
    divider("~3:40 PM — Goal for Mexico (score changes 0→1, detected within 60s)")
    match = make_match("IN_PLAY", 1, 0)
    event = {"type": "goal", "match": match, "scoring_team": MEXICO}
    embed = build_goal_embed(event, tracker)
    print_embed(embed)
    print("\n  [Telegram]")
    print(tg_goal(match, MEXICO, tracker))

    # ── Half time ─────────────────────────────────────────────────────────────
    divider("~3:47 PM — Half Time (status → PAUSED)")
    match = make_match("PAUSED", 1, 0, half_home=1, half_away=0)
    embed = build_halftime_embed(match, tracker)
    print_embed(embed)
    print("\n  [Telegram]")
    print("    ⏸️ HALF TIME")
    print("    🇲🇽 Mexico  1 — 0  🇿🇦 South Africa")
    print("    🏆 Group Stage · Group A · Matchday 1")

    # ── Second half ───────────────────────────────────────────────────────────
    divider("~4:02 PM — Second Half (status → IN_PLAY again)")
    match = make_match("IN_PLAY", 1, 0, half_home=1, half_away=0)
    embed = build_second_half_embed(match, tracker)
    print_embed(embed)
    print("\n  [Telegram]")
    print("    ▶️ SECOND HALF UNDERWAY")
    print("    🇲🇽 Mexico  1 — 0  🇿🇦 South Africa")
    print("    🏆 Group Stage · Group A · Matchday 1")

    if penalties:
        # ── Equaliser ─────────────────────────────────────────────────────────
        divider("~4:50 PM — Goal for South Africa (equaliser)")
        match = make_match("IN_PLAY", 1, 1, half_home=1, half_away=0)
        event = {"type": "goal", "match": match, "scoring_team": SOUTH_AFRICA}
        embed = build_goal_embed(event, tracker)
        print_embed(embed)

        # ── Full time (AET) ───────────────────────────────────────────────────
        divider("~5:00 PM — Full Time → Extra Time → Penalties")
        match = make_match("FINISHED", 1, 1, half_home=1, half_away=0, duration="PENALTY_SHOOTOUT")
        full_score = {
            "winner": "HOME_TEAM",
            "duration": "PENALTY_SHOOTOUT",
            "regularTime": {"home": 1, "away": 1},
            "extraTime": {"home": 0, "away": 0},
            "penalties": {"home": 4, "away": 3},
        }
        # fullTime must reflect cumulative total for the embed
        match["score"]["fullTime"] = {"home": 1, "away": 1}
        event = {"type": "fulltime", "match": match, "full_score": full_score}
        embed = build_fulltime_embed(event, tracker)
        print_embed(embed)
        print("\n  [Telegram]")
        print("    🏁 FULL TIME")
        print("    🇲🇽 Mexico  1 — 1  🇿🇦 South Africa")
        print("    🎉 Mexico wins! (on penalties)")
        print("    ⏱️ 90 min: 1 — 1")
        print("    ⏱️ AET: 1 — 1")
        print("    🥅 Pens: 4 — 3")

    else:
        # ── Full time (regular) ───────────────────────────────────────────────
        divider("~4:55 PM — Full Time")
        match = make_match("FINISHED", 1, 0, half_home=1, half_away=0)
        match["score"]["winner"] = "HOME_TEAM"
        full_score = {"winner": "HOME_TEAM", "duration": "REGULAR"}
        event = {"type": "fulltime", "match": match, "full_score": full_score}
        embed = build_fulltime_embed(event, tracker)
        print_embed(embed)
        print("\n  [Telegram]")
        print("    🏁 FULL TIME")
        print("    🇲🇽 Mexico  1 — 0  🇿🇦 South Africa")
        print("    🎉 Mexico wins!")
        print("    🏆 Group Stage · Group A · Matchday 1")

    print("\n" + "=" * 60)
    print("  All messages also sent to Telegram channel simultaneously.")
    print("  Actual timing: up to 60s delay after each real event.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--penalties", action="store_true", help="Simulate extra time + penalty shootout")
    args = parser.parse_args()
    run_simulation(penalties=args.penalties)

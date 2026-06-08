import datetime
import zoneinfo
import discord

COLOR_KICKOFF  = 0x00AA00   # green
COLOR_GOAL     = 0xFF6600   # orange
COLOR_HALFTIME = 0x0055FF   # blue
COLOR_FULLTIME = 0xCC0000   # NJIT red
COLOR_SCHEDULE = 0x888888   # gray
COLOR_GOLD     = 0xFFD700   # gold (for Final)

ET_ZONE = zoneinfo.ZoneInfo("America/New_York")
WC_EMBLEM = "https://crests.football-data.org/wm26.png"


def format_score(match: dict) -> str:
    home = match["score"]["fullTime"]["home"] or 0
    away = match["score"]["fullTime"]["away"] or 0
    return f"{home} — {away}"


def format_stage(stage: str) -> str:
    mapping = {
        "GROUP_STAGE":    "Group Stage",
        "LAST_32":        "Round of 32",
        "LAST_16":        "Round of 16 🔥",
        "ROUND_OF_16":    "Round of 16 🔥",
        "QUARTER_FINALS": "Quarter Finals 🔥🔥",
        "SEMI_FINALS":    "Semi Finals 🔥🔥🔥",
        "THIRD_PLACE":    "3rd Place Match",
        "FINAL":          "🏆 THE FINAL 🏆",
    }
    return mapping.get(stage, stage)


def _winner_from_match(
    match: dict,
    full_score: dict,
    home_name: str,
    away_name: str,
) -> tuple[str, str]:
    """Return (winner_name, result_text) using winner code first, then score comparison."""
    winner_code = full_score.get("winner") or match["score"].get("winner")
    ft_home = match["score"]["fullTime"]["home"] or 0
    ft_away = match["score"]["fullTime"]["away"] or 0

    if winner_code == "HOME_TEAM":
        return home_name, f"{home_name} wins!"
    if winner_code == "AWAY_TEAM":
        return away_name, f"{away_name} wins!"
    if winner_code == "DRAW":
        return "", "It's a draw!"
    if ft_home > ft_away:
        return home_name, f"{home_name} wins!"
    if ft_away > ft_home:
        return away_name, f"{away_name} wins!"
    return "", "It's a draw!"


def format_group(group: str) -> str:
    if not group:
        return "Knockout Stage"
    return group.replace("_", " ").title()


def kickoff_to_et(utc_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(
            utc_str.replace("Z", "+00:00")
        )
        et = dt.astimezone(ET_ZONE)
        return et.strftime("%-I:%M %p ET")
    except Exception:
        return utc_str


def build_kickoff_embed(match: dict, tracker) -> discord.Embed:
    home     = tracker.format_team_name(match["homeTeam"])
    away     = tracker.format_team_name(match["awayTeam"])
    stage    = format_stage(match.get("stage", ""))
    group    = format_group(match.get("group", "") or "")
    time_et  = kickoff_to_et(match["utcDate"])
    matchday = match.get("matchday")
    referees = match.get("referees", [])

    embed = discord.Embed(
        title="⚽ MATCH STARTING NOW!",
        description=f"# {home}  vs  {away}",
        color=COLOR_KICKOFF,
    )
    embed.add_field(name="🏆 Stage",   value=stage,   inline=True)
    if group and group != "Knockout Stage":
        embed.add_field(name="📋 Group", value=group, inline=True)
    embed.add_field(name="⏰ Kickoff", value=time_et, inline=True)
    if matchday:
        embed.add_field(name="📅 Matchday", value=str(matchday), inline=True)
    if referees:
        ref     = referees[0]
        ref_nat = ref.get("nationality", "")
        ref_str = ref.get("name", "")
        if ref_nat:
            ref_str += f" ({ref_nat})"
        embed.add_field(name="👨‍⚖️ Referee", value=ref_str, inline=True)
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed


def build_goal_embed(event: dict, tracker) -> discord.Embed:
    match    = event["match"]
    home     = tracker.format_team_name(match["homeTeam"])
    away     = tracker.format_team_name(match["awayTeam"])
    score    = format_score(match)
    stage    = format_stage(match.get("stage", ""))
    group    = format_group(match.get("group", "") or "")
    matchday = match.get("matchday")

    # Scoring team line — available from premium (team name) or free tier (scoring_team dict)
    scoring_team = event.get("scoring_team")
    team_name    = event.get("team", "")
    scorer_name  = event.get("scorer", "")
    minute       = event.get("minute")

    if scoring_team:
        goal_line = tracker.format_team_name(scoring_team)
    elif team_name:
        goal_line = team_name
    else:
        goal_line = None

    if scorer_name and minute:
        goal_detail = f"{scorer_name} ({minute}')"
    elif scorer_name:
        goal_detail = scorer_name
    elif minute:
        goal_detail = f"{minute}'"
    else:
        goal_detail = None

    embed = discord.Embed(
        title="⚽ GOOOOOAL!",
        description=f"# {home}  {score}  {away}",
        color=COLOR_GOAL,
    )
    if goal_line:
        embed.add_field(name="⚽ Goal", value=goal_line, inline=True)
    if goal_detail:
        embed.add_field(name="⏱️", value=goal_detail, inline=True)
    embed.add_field(name="🏆 Stage", value=stage, inline=True)
    if group and group != "Knockout Stage":
        embed.add_field(name="📋 Group", value=group, inline=True)
    if matchday:
        embed.add_field(name="📅 Matchday", value=str(matchday), inline=True)
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed


def build_halftime_embed(match: dict, tracker) -> discord.Embed:
    home     = tracker.format_team_name(match["homeTeam"])
    away     = tracker.format_team_name(match["awayTeam"])
    ht_home  = match["score"]["halfTime"]["home"] or 0
    ht_away  = match["score"]["halfTime"]["away"] or 0
    stage    = format_stage(match.get("stage", ""))
    group    = format_group(match.get("group", "") or "")
    matchday = match.get("matchday")

    embed = discord.Embed(
        title="⏸️ HALF TIME",
        description=f"# {home}  {ht_home} — {ht_away}  {away}",
        color=COLOR_HALFTIME,
    )
    embed.add_field(name="🏆 Stage", value=stage, inline=True)
    if group and group != "Knockout Stage":
        embed.add_field(name="📋 Group", value=group, inline=True)
    if matchday:
        embed.add_field(name="📅 Matchday", value=str(matchday), inline=True)
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed


def build_second_half_embed(match: dict, tracker) -> discord.Embed:
    home     = tracker.format_team_name(match["homeTeam"])
    away     = tracker.format_team_name(match["awayTeam"])
    ht_home  = match["score"]["halfTime"]["home"] or 0
    ht_away  = match["score"]["halfTime"]["away"] or 0
    stage    = format_stage(match.get("stage", ""))
    group    = format_group(match.get("group", "") or "")
    matchday = match.get("matchday")

    embed = discord.Embed(
        title="▶️ SECOND HALF UNDERWAY",
        description=f"# {home}  {ht_home} — {ht_away}  {away}",
        color=COLOR_KICKOFF,
    )
    embed.add_field(name="🏆 Stage", value=stage, inline=True)
    if group and group != "Knockout Stage":
        embed.add_field(name="📋 Group", value=group, inline=True)
    if matchday:
        embed.add_field(name="📅 Matchday", value=str(matchday), inline=True)
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed


def build_fulltime_embed(event: dict, tracker) -> discord.Embed:
    match      = event["match"]
    full_score = event.get("full_score") or {}

    home     = tracker.format_team_name(match["homeTeam"])
    away     = tracker.format_team_name(match["awayTeam"])
    stage    = format_stage(match.get("stage", ""))
    group    = format_group(match.get("group", "") or "")
    matchday = match.get("matchday")
    is_final = match.get("stage") == "FINAL"

    ft_home   = match["score"]["fullTime"]["home"] or 0
    ft_away   = match["score"]["fullTime"]["away"] or 0
    score_str = f"{ft_home} — {ft_away}"
    duration  = full_score.get("duration") or match["score"].get("duration", "REGULAR")

    winner, result_text = _winner_from_match(match, full_score, home, away)

    breakdown: list[str] = []
    winner_suffix = ""

    if duration == "PENALTY_SHOOTOUT":
        reg  = full_score.get("regularTime") or {}
        ext  = full_score.get("extraTime") or {}
        pens = full_score.get("penalties") or {}
        r_h, r_a = reg.get("home", 0) or 0, reg.get("away", 0) or 0
        e_h, e_a = ext.get("home", 0) or 0, ext.get("away", 0) or 0
        p_h, p_a = pens.get("home", 0) or 0, pens.get("away", 0) or 0
        breakdown = [
            f"⏱️ 90 min:           {r_h} — {r_a}",
            f"⏱️ After extra time: {r_h + e_h} — {r_a + e_a}",
            f"🥅 Penalties:        {p_h} — {p_a}",
        ]
        winner_suffix = " (on penalties)"

    elif duration == "EXTRA_TIME":
        reg = full_score.get("regularTime") or {}
        r_h, r_a = reg.get("home", 0) or 0, reg.get("away", 0) or 0
        breakdown = [
            f"⏱️ 90 min:           {r_h} — {r_a}",
            f"⏱️ After extra time: {ft_home} — {ft_away}",
        ]
        winner_suffix = " (AET)"

    result_line = (
        f"🎉 {result_text}{winner_suffix}" if winner else f"🤝 {result_text}"
    )

    if is_final and winner:
        embed = discord.Embed(
            title="🏆 WORLD CUP CHAMPIONS!",
            description=f"# 🎉 {winner}\n**2026 FIFA World Cup Champions!**",
            color=COLOR_GOLD,
        )
    else:
        embed = discord.Embed(
            title="🏁 FULL TIME",
            description=f"# {home}  {score_str}  {away}\n{result_line}",
            color=COLOR_FULLTIME,
        )

    embed.add_field(name="🏆 Stage", value=stage, inline=True)
    if group and group != "Knockout Stage":
        embed.add_field(name="📋 Group", value=group, inline=True)
    if matchday:
        embed.add_field(name="📅 Matchday", value=str(matchday), inline=True)
    if breakdown:
        embed.add_field(
            name="📊 Score Breakdown",
            value="\n".join(breakdown),
            inline=False,
        )
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed


def build_daily_schedule_embed(matches: list, tracker) -> discord.Embed:
    embed = discord.Embed(
        title="⚽ Today's World Cup Matches",
        color=COLOR_SCHEDULE,
    )
    if not matches:
        embed.description = "No matches today."
        embed.set_thumbnail(url=WC_EMBLEM)
        return embed

    for match in matches:
        home     = tracker.format_team_name(match["homeTeam"])
        away     = tracker.format_team_name(match["awayTeam"])
        time_et  = kickoff_to_et(match["utcDate"])
        stage    = format_stage(match.get("stage", ""))
        group    = format_group(match.get("group", "") or "")
        matchday = match.get("matchday")

        parts = [f"⏰ {time_et}", f"🏆 {stage}"]
        if group and group != "Knockout Stage":
            parts.append(group)
        if matchday:
            parts.append(f"Matchday {matchday}")

        embed.add_field(
            name=f"{home} vs {away}",
            value=" · ".join(parts),
            inline=False,
        )

    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="All times Eastern · FIFA WC 2026 · GSA Gateway")
    return embed


def build_standings_embed(standings_data: dict, tracker) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 FIFA World Cup 2026 — Group Standings",
        color=COLOR_SCHEDULE,
    )
    groups = standings_data.get("standings", [])
    for group in groups[:8]:  # cap at 8 groups
        group_name = group.get("group", "")
        label = format_group(group_name) if group_name else group.get("stage", "")
        table = group.get("table", [])
        lines = []
        for row in table[:4]:
            team  = row.get("team", {})
            tname = tracker.format_team_name(team)
            pts   = row.get("points", 0)
            pos   = row.get("position", "?")
            lines.append(f"{pos}. {tname} — {pts} pts")
        if lines:
            embed.add_field(name=label, value="\n".join(lines), inline=True)
    embed.set_thumbnail(url=WC_EMBLEM)
    embed.set_footer(text="FIFA World Cup 2026 · GSA Gateway")
    return embed

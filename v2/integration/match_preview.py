"""World Cup pre-match preview — pure formatter (no network, no I/O).

``build_match_preview`` turns a fixture + its two ``/competitions/WC/teams`` entries
+ a ``/matches/{id}/head2head`` payload + the group's standings rows into a single
ready-to-post string. The caller owns all I/O (fetching, tz formatting, venue lookup)
and passes the results in, so this module stays trivially unit-testable.

Channel-safety: plain numbered/`·`-separated lines, no code fences, no markdown
tables — GroupMe is plain text and Telegram/GroupMe can't render either. Bold ``**``
(only inside the reused ``format_standings``) is fine: GroupMe drops it.

Anti-fabrication: every optional line is OMITTED when its source is missing, never
faked. Head-to-head renders only from real ``aggregates`` counts, oriented to THIS
match's home/away at runtime; an unrecognised orientation degrades to the honest
"first competitive meeting" line rather than a guessed scoreline.
"""

from __future__ import annotations

from v2.integration.worldcup_tracker import team_label, format_standings

# football-data.org squad positions -> compact labels, in display order.
_POS = [("Goalkeeper", "GK"), ("Defence", "DEF"), ("Midfield", "MID"), ("Offence", "FWD")]


def _context_line(match: dict, kickoff_et: str) -> str:
    """`8:00 PM ET · Group G · Matchday 2` — each clause dropped when absent."""
    bits = [kickoff_et]
    group = match.get("group")
    if group:
        bits.append(group.replace("_", " ").title())
    elif match.get("stage"):
        bits.append(match["stage"].replace("_", " ").title())
    md = match.get("matchday")
    if md:
        bits.append(f"Matchday {md}")
    return " · ".join(b for b in bits if b)


def _referee_line(match: dict) -> str | None:
    refs = match.get("referees") or []
    main = next((r for r in refs if r.get("name")), None)
    if not main:
        return None
    nat = main.get("nationality")
    return f"👤 Ref: {main['name']}" + (f" ({nat})" if nat else "")


def _squad_summary(team: dict) -> str:
    """`26 players · GK 3 · DEF 9 · MID 7 · FWD 7` — counts only, always sum to total."""
    squad = team.get("squad") or []
    counts: dict[str, int] = {}
    for p in squad:
        counts[p.get("position") or "Other"] = counts.get(p.get("position") or "Other", 0) + 1
    parts = [f"{len(squad)} players"]
    for api_pos, label in _POS:
        if counts.get(api_pos):
            parts.append(f"{label} {counts[api_pos]}")
    # any non-standard / null positions
    other = sum(n for pos, n in counts.items() if pos not in {a for a, _ in _POS})
    if other:
        parts.append(f"Other {other}")
    return " · ".join(parts)


def _team_block(match_team: dict, team: dict | None) -> str | None:
    """Coach + squad-count block for one team, or None if we have no /teams data."""
    if not team:
        return None
    head = team_label(match_team)
    coach = (team.get("coach") or {}).get("name")
    if coach:
        head = f"{head} — Coach: {coach}"
    return f"{head}\n{_squad_summary(team)}"


def _h2h_line(h2h: dict | None, home: dict, away: dict) -> str:
    """Honest-partial head-to-head. Empty/unknown -> first-meeting line."""
    first_meeting = "First competitive meeting at a World Cup."
    agg = (h2h or {}).get("aggregates") or {}
    n = agg.get("numberOfMatches")
    if not n:
        return first_meeting
    a_home, a_away = agg.get("homeTeam") or {}, agg.get("awayTeam") or {}
    home_id, away_id = home.get("id"), away.get("id")
    # Orient the aggregate to THIS match's home/away (the API orients to the queried
    # fixture, but we verify rather than trust). Unrecognised -> degrade honestly.
    if a_home.get("id") == home_id and a_away.get("id") == away_id:
        hw, aw = a_home.get("wins", 0), a_away.get("wins", 0)
    elif a_home.get("id") == away_id and a_away.get("id") == home_id:
        hw, aw = a_away.get("wins", 0), a_home.get("wins", 0)
    else:
        return first_meeting
    draws = a_home.get("draws", 0)
    drword = "draw" if draws == 1 else "draws"
    return (f"Played {n} · {home.get('name')} {hw}–{aw} {away.get('name')} "
            f"· {draws} {drword}")


def build_match_preview(
    match: dict,
    home_team: dict | None,
    away_team: dict | None,
    h2h: dict | None,
    standings_rows: list[dict],
    venue: str | None,
    kickoff_et: str,
) -> str:
    """Compose the full preview post. All inputs are pre-fetched by the caller."""
    home, away = match["homeTeam"], match["awayTeam"]
    blocks: list[str] = ["🔮 MATCH PREVIEW"]

    # header: matchup + context + venue + referee
    header = [f"{team_label(home)} vs {team_label(away)}", _context_line(match, kickoff_et)]
    if venue:
        header.append(f"📍 {venue}")
    ref = _referee_line(match)
    if ref:
        header.append(ref)
    blocks.append("\n".join(header))

    # standings (reuse the shared, channel-safe renderer)
    table = format_standings(match.get("group") or "", standings_rows or [])
    if table:
        blocks.append(table)

    # head-to-head
    blocks.append("🤝 Head-to-head\n" + _h2h_line(h2h, home, away))

    # squads + coaches
    for mt, team in ((home, home_team), (away, away_team)):
        block = _team_block(mt, team)
        if block:
            blocks.append(block)

    return "\n\n".join(blocks)

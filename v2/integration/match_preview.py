"""World Cup pre-match preview — pure formatter (no network, no I/O).

Minimal by design: the matchup, the kickoff/group context, and the live group
table. The caller pre-fetches the standings rows and the formatted kickoff time
and passes them in, so this module stays trivially unit-testable.

Channel-safety: plain lines, no code fences, no markdown tables — GroupMe is
plain text and Telegram/GroupMe can't render either. Bold ``**`` (only inside the
reused ``format_standings``) is fine: GroupMe drops it.
"""

from __future__ import annotations

from v2.integration.worldcup_tracker import team_label, format_standings


def _context_line(match: dict, kickoff_et: str) -> str:
    """`1:00 PM ET · Group J · Matchday 2` — each clause dropped when absent."""
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


def build_match_preview(match: dict, standings_rows: list[dict], kickoff_et: str) -> str:
    """Compose the preview post: header + matchup + context, then the group table."""
    home, away = match["homeTeam"], match["awayTeam"]
    header = (f"⏳ MATCH PREVIEW\n{team_label(home)} vs {team_label(away)}\n"
              f"{_context_line(match, kickoff_et)}")
    blocks = [header]
    table = format_standings(match.get("group") or "", standings_rows or [])
    if table:
        blocks.append(table)
    return "\n\n".join(blocks)

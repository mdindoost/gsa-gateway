"""Shadow comparator: ESPN NormMatch vs football-data match dicts (pure, no network).

Read-only A/B. Joins the two sources by KICKOFF time (schedule data, reliable in both;
team-name spellings differ so they can't be the join key) and reports per match whether
the live score + state agree. The standalone shadow script (scripts/wc_shadow_compare.py)
calls this each tick and accumulates first-seen-per-goal timestamps to measure which
source reflects a goal first. Nothing here posts.
"""
from __future__ import annotations

from v2.integration.wc_providers.normalize import NormMatch

# football-data raw status → canonical, reusing the live engine's single source of truth.
from v2.integration.match_watcher import _canon as _fd_canon


def kickoff_key(utc_date: str) -> str:
    """Normalize a kickoff timestamp to minute precision so ESPN ('...T19:00Z') and
    football-data ('...T19:00:00Z') land on the same key."""
    return (utc_date or "")[:16]   # "YYYY-MM-DDTHH:MM"


def _fd_score(m: dict) -> tuple[int, int]:
    ft = (m.get("score") or {}).get("fullTime") or {}
    return (ft.get("home") or 0, ft.get("away") or 0)


def _tokens(*names: str) -> set[str]:
    """Lowercased word tokens of team names, for cross-source team matching (spellings
    differ: 'Bosnia-Herzegovina' vs 'Bosnia-H.', 'United States' vs 'USA')."""
    out: set[str] = set()
    for n in names:
        for w in (n or "").lower().replace("-", " ").replace(".", " ").split():
            if len(w) > 1:          # drop initials like the 'h' in 'Bosnia-H.'
                out.add(w)
    return out


def compare(espn_matches: list[NormMatch], fd_matches: list[dict]) -> list[dict]:
    """Join by kickoff AND best team-name overlap (FIFA runs paired simultaneous kickoffs,
    so kickoff alone is not unique). Each football-data match is consumed once."""
    # group fd matches by kickoff so we resolve same-kickoff pairs by team identity
    fd_by_key: dict[str, list[dict]] = {}
    for m in fd_matches:
        fd_by_key.setdefault(kickoff_key(m.get("utcDate", "")), []).append(m)
    rows: list[dict] = []
    for em in espn_matches:
        key = kickoff_key(em.utc_date)
        pool = fd_by_key.get(key) or []
        fm = None
        if pool:
            etok = _tokens(em.home.name, em.away.name)
            fm = max(pool, key=lambda m: len(
                etok & _tokens(m.get("homeTeam", {}).get("name"),
                               m.get("awayTeam", {}).get("name"))))
            # require at least one shared team token to count as a match
            if etok & _tokens(fm.get("homeTeam", {}).get("name"),
                              fm.get("awayTeam", {}).get("name")):
                pool.remove(fm)
            else:
                fm = None
        if fm is None:
            rows.append({"matched": False, "source": "espn-only", "kickoff": key,
                         "teams": f"{em.home.name} v {em.away.name}",
                         "espn_score": em.score, "espn_state": em.state})
            continue
        fd_score = _fd_score(fm)
        rows.append({
            "matched": True, "kickoff": key,
            "teams": f"{em.home.name} v {em.away.name}",
            "espn_score": em.score, "fd_score": fd_score,
            "scores_agree": em.score == fd_score,
            "espn_state": em.state, "fd_state": _fd_canon(fm.get("status")),
            "states_agree": em.state == _fd_canon(fm.get("status")),
        })
    return rows

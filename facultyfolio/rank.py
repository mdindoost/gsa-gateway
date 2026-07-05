"""Department ranking + coverage denominator (spec §8).

One source of truth for the leaderboard. Rank is NOT shown on personal pages
(spec §4) — it lives only on the leaderboard, where ranking is the explicit purpose.
"""
import json

from . import config
from .db import connect
from .format import normalize_name


_FACULTY_LABEL = "Faculty"                       # catch-all for rank-less titles (Director, empty)


def rank_of(title: str):
    """Map a title string to (rank_index, rank_label) on config.RANK_LADDER.

    Pass 1: "Department Chair" -> group 0 (heads the unit, overrides professorial rank).
    Pass 2: professorial phrases, longest-first (substring-safe: "Associate Professor" is
            searched before bare "Professor"; resolves compounds like "…, Associate Dean").
    Pass 3: a bare "Dean" -> Professor (a dean holds a full professorship).
    Else  : the "Faculty" catch-all (index just past the ladder).
    """
    t = (title or "").lower()
    ladder = config.RANK_LADDER
    if "department chair" in t:
        return 0, ladder[0]
    for phrase in sorted(ladder[1:], key=len, reverse=True):        # professorial, longest-first
        if phrase.lower() in t:
            return ladder.index(phrase), phrase
    if "dean" in t:
        return ladder.index("Professor"), "Professor"
    return len(ladder), _FACULTY_LABEL


def _members(conn, org_id):
    """(slug, name, scholar-dict|{}) for active home faculty of the org."""
    rows = conn.execute(
        """SELECT n.key AS key, n.name AS name, n.attrs AS attrs FROM nodes n
           JOIN edges e ON e.src_id=n.id
           WHERE n.type='Person' AND n.is_active=1
             AND e.type='has_role' AND e.category='faculty'
             AND e.dst_id=? AND e.is_active=1""",
        (org_id,),
    ).fetchall()
    out = []
    for r in rows:
        slug = r["key"].split("/")[-1]
        if slug in config.SUPPRESSED:
            continue
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        scholar = (attrs.get("profiles", {}) or {}).get("scholar", {}) or {}
        out.append((slug, normalize_name(r["name"]), scholar))
    return out


def coverage(org_id) -> tuple:
    """(N with Scholar citations, M total home faculty)."""
    conn = connect()
    try:
        members = _members(conn, org_id)
    finally:
        conn.close()
    M = len(members)
    N = sum(1 for _, _, sch in members if isinstance(sch.get("citations"), int))
    return N, M


def ranked_list(org_id) -> list:
    """Members with Scholar citations, ranked by total citations descending."""
    conn = connect()
    try:
        members = _members(conn, org_id)
    finally:
        conn.close()
    scored = [
        {"slug": s, "name": nm, "citations": sch["citations"], "h_index": sch.get("h_index")}
        for s, nm, sch in members
        if isinstance(sch.get("citations"), int)
    ]
    scored.sort(key=lambda r: (-r["citations"], r["name"]))
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    return scored

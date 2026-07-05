"""Department ranking + coverage denominator (spec §8).

One source of truth for the leaderboard. Rank is NOT shown on personal pages
(spec §4) — it lives only on the leaderboard, where ranking is the explicit purpose.
"""
import json

from . import config
from . import db
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


def roster(org_id) -> list:
    """Every in-scope faculty member as a leaderboard-ready dict (spec §4).

    Unlike `ranked_list`, this includes the no-Scholar faculty (citations=None) so
    all views can show the full department. Title comes from `get_faculty` so it
    matches the person's profile page verbatim; rank_index/label from `rank_of`.
    Scope is CS for now (`db.cs_faculty_slugs`); org_id is carried for future depts.
    """
    out = []
    for slug in db.cs_faculty_slugs():
        f = db.get_faculty(slug)
        idx, label = rank_of(f["title"])
        sch = f["scholar"] or {}
        out.append({
            "slug": f["slug"],
            "name": f["name"],
            "title": f["title"],
            "rank_index": idx,
            "rank_label": label,
            "citations": sch.get("citations") if sch else None,
            "h_index": sch.get("h_index") if sch else None,
            "areas": f["areas"],
        })
    return out


def _surname(name: str) -> str:
    return (name or "").split()[-1].casefold() if (name or "").split() else ""


def by_rank(roster) -> list:
    """Group the roster into ladder buckets, seniority order (empty groups dropped).

    Members within a group sort by surname, then full name, then slug (byte-stable).
    The 'Faculty' catch-all (rank-less titles) sorts last, after the ladder.
    """
    groups = {}
    for row in roster:
        groups.setdefault((row["rank_index"], row["rank_label"]), []).append(row)
    out = []
    for (index, label) in sorted(groups):                      # ladder order = ascending index
        members = sorted(groups[(index, label)],
                         key=lambda r: (_surname(r["name"]), r["name"].casefold(), r["slug"]))
        out.append({"index": index, "label": label, "members": members})
    return out


def by_citations(roster) -> list:
    """Scholar faculty ranked by citations desc (1..N), then the no-Scholar tail A–Z.

    None-safe: no-Scholar rows sort last (citations is None -> True). Each Scholar row
    gets a 1-based `rank_num`; unranked rows get `rank_num=None`.
    """
    ordered = sorted(
        roster,
        key=lambda r: (r["citations"] is None, -(r["citations"] or 0), r["name"].casefold(), r["slug"]),
    )
    n = 0
    out = []
    for r in ordered:
        row = dict(r)
        if r["citations"] is None:
            row["rank_num"] = None
        else:
            n += 1
            row["rank_num"] = n
        out.append(row)
    return out


def by_name(roster) -> list:
    """All faculty A–Z by surname, then full name, then slug (byte-stable)."""
    return sorted(roster, key=lambda r: (_surname(r["name"]), r["name"].casefold(), r["slug"]))


def leaderboard_stats(roster, coverage) -> dict:
    """At-a-glance strip data (spec §5). Pure — no DB.

    total / with_scholar come from `coverage` (the canonical N-of-M denominator, same
    one the profile pages show); per-rank group counts from `by_rank(roster)`.
    """
    n, m = coverage
    return {
        "total": m,
        "with_scholar": n,
        "groups": [(g["label"], len(g["members"])) for g in by_rank(roster)],
    }


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

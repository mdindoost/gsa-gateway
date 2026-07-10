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
    Enumerates the org given by `org_id` (home faculty of that dept).
    """
    out = []
    for slug in db.faculty_slugs(org_id):
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
            # momentum inputs (★ Rising view) — carried so rising() needs no extra DB pass
            "cites_per_year": sch.get("cites_per_year") if sch else None,
            "updated_at": sch.get("updated_at") if sch else None,
        })
    return out


def rising(roster) -> tuple:
    """The ★ Rising view: (riser rows sorted by momentum desc, funnel counts).

    Delegates the mechanical math to `momentum.rising_view`; the roster rows must carry
    `cites_per_year` + `updated_at` (added in `roster` above). Returns the same
    `(rows, {"risers","gated","scholar","total"})` shape the renderer consumes."""
    from . import momentum
    return momentum.rising_view(roster)


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
                         key=lambda r: (_surname(r["name"]), (r["name"] or "").casefold(), r["slug"]))
        out.append({"index": index, "label": label, "members": members})
    return out


def by_citations(roster) -> list:
    """Scholar faculty ranked by citations desc (1..N), then the no-Scholar tail A–Z.

    None-safe: no-Scholar rows sort last (citations is None -> True). Each Scholar row
    gets a 1-based `rank_num`; unranked rows get `rank_num=None`.
    """
    ordered = sorted(
        roster,
        key=lambda r: (r["citations"] is None, -(r["citations"] or 0), (r["name"] or "").casefold(), r["slug"]),
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
    return sorted(roster, key=lambda r: (_surname(r["name"]), (r["name"] or "").casefold(), r["slug"]))


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


def college_rollup(college_node) -> dict:
    """College-wide rank rollup: concat every in-scope roster, rank ONCE.

    Org set = department children + the college node itself (mirrors db.college_coverage,
    catching faculty homed directly on the college org, e.g. a deptless college). Reusing
    by_rank on the combined list makes ladder order correct by construction — no merge logic.
    """
    org_ids = [d["node_id"] for d in db.dept_orgs_of_college(college_node)] + [college_node]
    combined = [row for oid in org_ids for row in roster(oid)]
    slugs = {r["slug"] for r in combined}
    assert len(slugs) == len(combined), (
        f"college_rollup: {len(combined) - len(slugs)} duplicate-home person(s) "
        "(multi-home producer regression) — the faculty headcount would inflate")
    return {
        "total": len(combined),
        "with_scholar": sum(1 for r in combined if r["citations"] is not None),
        "groups": [(g["label"], len(g["members"])) for g in by_rank(combined)],
    }


def college_chairs(college_node) -> list:
    """Every department chair (the rank_index==0 group members) tagged with dept_name.
    0 chairs in a dept -> none contributed; >1 -> all, surname-sorted."""
    out = []
    for d in db.dept_orgs_of_college(college_node):
        chairs = [r for r in roster(d["node_id"]) if r["rank_index"] == 0]
        for c in sorted(chairs, key=lambda r: (_surname(r["name"]), (r["name"] or "").casefold(), r["slug"])):
            row = dict(c)
            row["dept_name"] = d["name"]
            out.append(row)
    return out


def funding_rollup(org_ids):
    """Aggregate NSF+NIH funding across the given org node ids' home faculty.
    Dedup by person node id (a dup-home person counted once). Returns
    {nsf, nih, n_funded, as_of} or None when nothing is funded."""
    conn = connect()
    seen = {}
    try:
        for oid in org_ids:
            for r in conn.execute(
                """SELECT n.id AS id, n.key AS key, n.attrs AS attrs FROM nodes n
                   JOIN edges e ON e.src_id=n.id
                   WHERE n.type='Person' AND n.is_active=1
                     AND e.type='has_role' AND e.category='faculty'
                     AND e.dst_id=? AND e.is_active=1""", (oid,)):
                if r["id"] in seen or r["key"].split("/")[-1] in config.SUPPRESSED:
                    continue
                fund = (json.loads(r["attrs"]) if r["attrs"] else {}).get("funding") or {}
                nsf = int((fund.get("nsf") or {}).get("njit_total") or 0)
                nih = int((fund.get("nih") or {}).get("njit_total") or 0)
                dates = [b["updated_at"] for b in (fund.get("nsf"), fund.get("nih"))
                         if b and b.get("updated_at")]
                seen[r["id"]] = (nsf, nih, dates)
    finally:
        conn.close()
    nsf_t = sum(v[0] for v in seen.values())
    nih_t = sum(v[1] for v in seen.values())
    if nsf_t == 0 and nih_t == 0:
        return None
    n_funded = sum(1 for v in seen.values() if v[0] > 0 or v[1] > 0)
    all_dates = [d for v in seen.values() if v[0] > 0 or v[1] > 0 for d in v[2]]  # counted bags only
    return {"nsf": nsf_t, "nih": nih_t, "n_funded": n_funded,
            "as_of": min(all_dates) if all_dates else None}

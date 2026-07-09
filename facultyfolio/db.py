"""Data layer — read a faculty node from the KG into a clean dict.

The ONLY module that touches SQLite. Connection is opened read-only at the driver
level so a bug cannot write (spec §1). Formatters are NOT applied here (render owns
presentation) except normalize_name, which is identity-level.
"""
import json
import re
import sqlite3

from . import config
from .format import normalize_name

_PROSE_TYPES = ("education", "teaching", "profile", "research_statement")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _area_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _resolve(conn, id_or_slug):
    if isinstance(id_or_slug, int):
        return conn.execute("SELECT * FROM nodes WHERE id=?", (id_or_slug,)).fetchone()
    return conn.execute(
        "SELECT * FROM nodes WHERE type='Person' AND key=?",
        (f"people.njit.edu/profile/{id_or_slug}",),
    ).fetchone()


def _org_name(conn, org_id):
    r = conn.execute("SELECT name FROM nodes WHERE id=?", (org_id,)).fetchone()
    return r["name"] if r else None


def _org_slug(conn, node_id):
    """The organizations.slug for an Org node (via nodes.attrs.org_id). None if absent."""
    r = conn.execute("SELECT attrs FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r or not r["attrs"]:
        return None
    oid = json.loads(r["attrs"]).get("org_id")
    if oid is None:
        return None
    s = conn.execute("SELECT slug FROM organizations WHERE id=?", (oid,)).fetchone()
    return s["slug"] if s else None


def _college_of(conn, org_id):
    """First part_of parent of the department org (e.g. CS -> YWCC)."""
    r = conn.execute(
        "SELECT dst_id FROM edges WHERE src_id=? AND type='part_of' AND is_active=1 LIMIT 1",
        (org_id,),
    ).fetchone()
    if not r:
        return None
    short = _org_name(conn, r["dst_id"])
    return config.COLLEGE_NAMES.get(short, short)     # proper-noun expansion, else verbatim


def _prose(conn, key, ptype):
    rows = conn.execute(
        """SELECT content FROM knowledge_items
           WHERE type=? AND is_active=1 AND created_by='crawler'
             AND metadata LIKE ? ORDER BY id""",
        (ptype, f'%"entity_id": "{key}"%'),
    ).fetchall()
    return rows[0]["content"] if rows else ""


def get_faculty(id_or_slug) -> dict:
    conn = connect()
    try:
        node = _resolve(conn, id_or_slug)
        if node is None:
            raise KeyError(f"faculty not found: {id_or_slug}")
        key = node["key"]
        slug = key.split("/")[-1]
        attrs = json.loads(node["attrs"]) if node["attrs"] else {}
        profiles = attrs.get("profiles", {}) or {}

        # roles
        home_dept = joint_dept = title = home_dept_segment = None
        affiliated_depts = []
        for e in conn.execute(
            "SELECT * FROM edges WHERE src_id=? AND type='has_role' AND is_active=1 ORDER BY id",
            (node["id"],),
        ):
            eattrs = json.loads(e["attrs"]) if e["attrs"] else {}
            titles = [t for t in (eattrs.get("titles") or []) if t]
            if e["category"] == "faculty":
                home_dept = _org_name(conn, e["dst_id"])
                title = ", ".join(titles) if titles else None
                college = _college_of(conn, e["dst_id"])
                home_dept_segment = _org_slug(conn, e["dst_id"])
            elif e["category"] == "joint" and joint_dept is None:
                joint_dept = _org_name(conn, e["dst_id"])
            elif e["category"] == "affiliated":
                nm = _org_name(conn, e["dst_id"])
                if nm and nm not in affiliated_depts:
                    affiliated_depts.append(nm)
        college = college if home_dept else None

        # research areas — active edges, edge-id order, mechanical near-dup collapse
        areas, seen = [], set()
        for e in conn.execute(
            "SELECT dst_id FROM edges WHERE src_id=? AND type='researches' AND is_active=1 ORDER BY id",
            (node["id"],),
        ):
            nm = _org_name(conn, e["dst_id"])
            if not nm:
                continue
            k = _area_key(nm)
            if k in seen:
                continue
            seen.add(k)
            areas.append(nm)

        # scholar bag (only "has Scholar" when citations are populated)
        scholar = profiles.get("scholar") or {}
        scholar = scholar if isinstance(scholar.get("citations"), int) else None

        education_raw = _prose(conn, key, "education")
        teaching_raw = _prose(conn, key, "teaching")
        research_statement_raw = _prose(conn, key, "research_statement")
        prose_types = [t for t in _PROSE_TYPES if _prose(conn, key, t)]

        # Recognition (crawler-only): award TITLES (one row each, clean verbatim strings) +
        # the single service prose blob. Formatting/de-noise is applied in render.py.
        awards_raw = [r["title"] for r in conn.execute(
            """SELECT title FROM knowledge_items
               WHERE type='award' AND is_active=1 AND created_by='crawler'
                 AND metadata LIKE ? ORDER BY id""",
            (f'%"entity_id": "{key}"%',))]
        service_raw = _prose(conn, key, "service")

        return {
            "slug": slug,
            "name": normalize_name(node["name"]),
            "title": title,
            "home_dept": home_dept,
            "home_dept_segment": home_dept_segment,
            "joint_dept": joint_dept,
            "affiliated_depts": affiliated_depts,
            "college": college,
            "email": attrs.get("email"),
            "phone": attrs.get("phone"),
            "office": attrs.get("office"),
            "profiles": profiles,
            "areas": areas,
            "education_raw": education_raw,
            "teaching_raw": teaching_raw,
            "research_statement_raw": research_statement_raw,
            "awards_raw": awards_raw,
            "service_raw": service_raw,
            "scholar": scholar,
            "suppressed": slug in config.SUPPRESSED,
            "_prose_types": prose_types,
        }
    finally:
        conn.close()


def faculty_slugs(org_id) -> list:
    """Slugs of an org's home faculty (has_role category='faculty' -> org), minus suppressed."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT n.key AS key FROM nodes n
               JOIN edges e ON e.src_id=n.id
               WHERE n.type='Person' AND n.is_active=1
                 AND e.type='has_role' AND e.category='faculty'
                 AND e.dst_id=? AND e.is_active=1
               ORDER BY n.name""",
            (org_id,),
        ).fetchall()
    finally:
        conn.close()
    slugs = [r["key"].split("/")[-1] for r in rows]
    return [s for s in slugs if s not in config.SUPPRESSED]


def cs_faculty_slugs() -> list:
    """CS home-faculty slugs. Thin alias over faculty_slugs (kept for existing callers/tests)."""
    return faculty_slugs(config.CS_ORG_ID)


def org_node_by_slug(slug):
    """The nodes.id of the Org whose organizations.slug == slug (None if not found)."""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT n.id AS id FROM nodes n
               JOIN organizations o ON o.id = json_extract(n.attrs, '$.org_id')
               WHERE n.type='Org' AND o.slug=? LIMIT 1""",
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


def dept_orgs_of_college(college_node_id) -> list:
    """Department child Orgs of a college (part_of), with faculty>0, sorted by slug."""
    conn = connect()
    try:
        child_ids = [r["id"] for r in conn.execute(
            """SELECT n.id AS id FROM nodes n
               JOIN edges e ON e.src_id=n.id
               WHERE e.type='part_of' AND e.dst_id=? AND e.is_active=1
                 AND n.type='Org' AND n.is_active=1""",
            (college_node_id,),
        ).fetchall()]
        out = []
        for nid in child_ids:
            fac = conn.execute(
                """SELECT COUNT(DISTINCT n2.id) FROM nodes n2
                   JOIN edges e2 ON e2.src_id=n2.id
                   WHERE e2.type='has_role' AND e2.category='faculty'
                     AND e2.dst_id=? AND e2.is_active=1 AND n2.is_active=1""",
                (nid,),
            ).fetchone()[0]
            # NOTE: `fac` counts ALL home faculty (not SUPPRESSED-filtered). With SUPPRESSED
            # empty this equals rank.coverage's denominator; if it's ever populated a dept whose
            # only faculty are suppressed would still pass this gate (empty leaderboard). Accepted.
            if fac > 0:
                out.append({"node_id": nid, "slug": _org_slug(conn, nid),
                            "name": _org_name(conn, nid), "faculty": fac})
    finally:
        conn.close()
    out.sort(key=lambda d: d["slug"] or "")
    return out


def college_coverage(college_node_id) -> tuple:
    """(N distinct home faculty with Scholar citations, M distinct home faculty) across the
    college node itself and every faculty>0 child org. DISTINCT by person id, so a faculty
    homed in two child orgs (the known dup-home case) is counted once."""
    import json
    org_ids = [college_node_id] + [d["node_id"] for d in dept_orgs_of_college(college_node_id)]
    conn = connect()
    try:
        placeholders = ",".join("?" for _ in org_ids)
        rows = conn.execute(
            f"""SELECT DISTINCT n.id AS id, n.key AS key, n.attrs AS attrs FROM nodes n
                JOIN edges e ON e.src_id=n.id
                WHERE n.type='Person' AND n.is_active=1
                  AND e.type='has_role' AND e.category='faculty'
                  AND e.dst_id IN ({placeholders}) AND e.is_active=1""",
            org_ids,
        ).fetchall()
    finally:
        conn.close()
    m, n = 0, 0
    for r in rows:
        slug = r["key"].split("/")[-1]
        if slug in config.SUPPRESSED:
            continue
        m += 1
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        sch = (attrs.get("profiles", {}) or {}).get("scholar", {}) or {}
        if isinstance(sch.get("citations"), int):
            n += 1
    return n, m


def college_name(college_node_id) -> str:
    """Org node name, expanded via config.COLLEGE_NAMES (acronym -> full college name)."""
    conn = connect()
    try:
        short = _org_name(conn, college_node_id) or ""
    finally:
        conn.close()
    return config.COLLEGE_NAMES.get(short, short)

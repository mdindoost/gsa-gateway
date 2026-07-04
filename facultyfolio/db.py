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

_PROSE_TYPES = ("education", "teaching", "profile")


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
             AND metadata LIKE ?""",
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
        home_dept = joint_dept = title = None
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
            elif e["category"] == "joint" and joint_dept is None:
                joint_dept = _org_name(conn, e["dst_id"])
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
        prose_types = [t for t in _PROSE_TYPES if _prose(conn, key, t)]

        return {
            "slug": slug,
            "name": normalize_name(node["name"]),
            "title": title,
            "home_dept": home_dept,
            "joint_dept": joint_dept,
            "college": college,
            "email": attrs.get("email"),
            "phone": attrs.get("phone"),
            "office": attrs.get("office"),
            "profiles": profiles,
            "areas": areas,
            "education_raw": education_raw,
            "teaching_raw": teaching_raw,
            "scholar": scholar,
            "suppressed": slug in config.SUPPRESSED,
            "_prose_types": prose_types,
        }
    finally:
        conn.close()


def cs_faculty_slugs() -> list:
    """Slugs of CS home faculty (category='faculty' -> CS org), minus suppressed."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT n.key AS key FROM nodes n
               JOIN edges e ON e.src_id=n.id
               WHERE n.type='Person' AND n.is_active=1
                 AND e.type='has_role' AND e.category='faculty'
                 AND e.dst_id=? AND e.is_active=1
               ORDER BY n.name""",
            (config.CS_ORG_ID,),
        ).fetchall()
    finally:
        conn.close()
    slugs = [r["key"].split("/")[-1] for r in rows]
    return [s for s in slugs if s not in config.SUPPRESSED]

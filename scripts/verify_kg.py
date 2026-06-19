#!/usr/bin/env python
"""Mandatory post-build alignment check: the gathered graph must agree with the KB.

Catches the class of bug Amy Hoover exposed (KB filed under the wrong department) and
missing/extra people. Returns the list of problems; the CLI exits non-zero if any, so a
gather/refresh can gate on it. Listings are authoritative (appointments come from them), so
"aligned" means: every person's KB sits under a department they're appointed in, and every
person with a department appointment has KB content.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _dept_org_ids(conn: sqlite3.Connection, person_node_id: int) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT json_extract(o.attrs,'$.org_id') FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1 "
        "AND json_extract(o.attrs,'$.org_id') IN "
        "    (SELECT id FROM organizations WHERE type='department')", (person_node_id,))}


def _kb_org_ids(conn: sqlite3.Connection, person_key: str) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT org_id FROM knowledge_items WHERE is_active=1 "
        "AND json_extract(metadata,'$.entity_id')=?", (person_key,))}


def verify_kg(conn: sqlite3.Connection) -> list[str]:
    """Return alignment problems (empty list = aligned)."""
    issues: list[str] = []
    for p in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        dept = _dept_org_ids(conn, p[0])
        kb = _kb_org_ids(conn, p[1])
        if kb and dept and not (kb <= dept):
            issues.append(f"mis-filed KB: {p[2]} filed in {sorted(kb)}, "
                          f"not in dept appointment(s) {sorted(dept)}")
        if dept and not kb:
            issues.append(f"no KB content: {p[2]} has a department appointment but no items")
    issues.extend(_verify_mtsm_no_departments(conn))
    issues.extend(_verify_org_tree(conn))
    return issues


def _verify_org_tree(conn: sqlite3.Connection) -> list[str]:
    """Invariant (multi-college expansion): no college/department/school org is an orphan, and
    every college sits under the `njit` root. An orphan (parent_id=NULL) means `ensure_org`
    couldn't resolve a parent — a person filed there would be unreachable by college/department
    traversal."""
    issues: list[str] = []
    njit = conn.execute("SELECT id FROM organizations WHERE slug='njit'").fetchone()
    njit_id = njit[0] if njit else None
    for slug in ("nce", "csla", "hcad", "mtsm"):
        row = conn.execute("SELECT parent_id FROM organizations WHERE slug=? AND is_active=1",
                           (slug,)).fetchone()
        if row and njit_id and row[0] != njit_id:
            issues.append(f"org tree: college '{slug}' parent_id={row[0]} is not njit ({njit_id})")
    for slug, otype in conn.execute(
            "SELECT slug, type FROM organizations WHERE is_active=1 "
            "AND type IN ('college','department','school') AND parent_id IS NULL"):
        issues.append(f"org tree: orphan org '{slug}' (type={otype}, parent_id=NULL)")
    return issues


def _verify_mtsm_no_departments(conn: sqlite3.Connection) -> list[str]:
    """Invariant: MTSM must have ZERO type='department' children. MTSM files faculty KB under
    the college org (mtsm) because it has no departments; if a department child ever appears,
    `_home_dept_org_id` would start filing faculty KB under it while appointments stay on the
    college, silently desyncing 'MTSM faculty' queries (C1 in the design review)."""
    row = conn.execute("SELECT id FROM organizations WHERE slug='mtsm' AND is_active=1").fetchone()
    if not row:
        return []
    depts = [r[0] for r in conn.execute(
        "SELECT name FROM organizations WHERE parent_id=? AND is_active=1 AND type='department'",
        (row[0],))]
    if depts:
        return [f"MTSM invariant violated: mtsm has type='department' child(ren) {depts} — "
                "faculty KB will desync from appointments. Re-file them as 'program'/'unit'."]
    return []


def verify_gsa(conn: sqlite3.Connection) -> list[str]:
    """GSA-specific alignment: the GSA org has at least one officer, and no legacy QA
    (type='faq') remains active under GSA. Empty list = aligned."""
    issues: list[str] = []
    g = conn.execute("SELECT id FROM organizations WHERE slug='gsa' AND is_active=1").fetchone()
    if not g:
        return ["no GSA org found"]
    gid = g[0]
    officers = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.type='has_role' AND e.is_active=1 AND e.category IN ('officer','deprep') "
        "AND json_extract(o.attrs,'$.org_id')=?", (gid,)).fetchone()[0]
    if officers == 0:
        issues.append("no GSA officers in the graph (roster not ingested?)")
    leftover = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE org_id=? AND type='faq' AND is_active=1",
        (gid,)).fetchone()[0]
    if leftover:
        issues.append(f"{leftover} active GSA QA item(s) remain (not retired)")
    return issues


def main() -> int:
    import argparse
    from v2.core.database.schema import get_connection
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    args = ap.parse_args()
    issues = verify_kg(get_connection(args.db))
    if not issues:
        print("✓ KG aligned with KB — no mis-filed or missing people.")
        return 0
    print(f"✗ {len(issues)} alignment problem(s):")
    for i in issues:
        print("  -", i)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

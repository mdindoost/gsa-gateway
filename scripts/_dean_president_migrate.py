"""One-time live-data migration: apply Option A to existing data + seed the NJIT President.

Two changes, both idempotent (safe to re-run):

  1. Re-point existing Dean / Associate-Dean admin appointments from the
     'College Administration' sub-unit to the parent college (YWCC). The
     crawler now files these on the parent org (Option A), but data gathered
     before that change still points the admin edge at college-administration.

  2. Seed Teik C. Lim as NJIT President — a manual (source/created_by='dashboard')
     Person node + has_role->NJIT admin edge + a knowledge_item under NJIT so he
     shows in the KB tab and is embedded into the RAG corpus. Source-tagged
     'dashboard' so a future crawler --reset never wipes him.

Dry run by default (reports the plan, writes nothing). --commit takes a hardened
backup first, then writes. After --commit, run:
    python v2/scripts/embed_all.py        # embeds the new President item (resumable)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.project import project_appointment

PRESIDENT_KEY = "manual/teik-c-lim"
PRESIDENT_NAME = "Teik C. Lim"
PRESIDENT_TITLE = "President"
PRESIDENT_URL = "https://www.njit.edu/president"
PRESIDENT_CONTENT = (
    "Profile: Teik C. Lim — President, New Jersey Institute of Technology (NJIT). "
    "Teik C. Lim is the President of NJIT, the university that Ying Wu College of "
    "Computing (YWCC) is part of."
)


def _org_node(conn, slug: str):
    r = conn.execute("SELECT id FROM nodes WHERE type='Org' AND key=?", (slug,)).fetchone()
    return r[0] if r else None


def _dean_edges(conn):
    """Active admin has_role edges still pointing at college-administration (Option A targets)."""
    ca = _org_node(conn, "college-administration")
    if ca is None:
        return []
    return conn.execute(
        "SELECT e.id, p.name, e.attrs FROM edges e JOIN nodes p ON p.id=e.src_id "
        "WHERE e.type='has_role' AND e.dst_id=? AND e.category='admin' AND e.is_active=1 "
        "ORDER BY p.name", (ca,)).fetchall()


def _president_exists(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM knowledge_items WHERE json_extract(metadata,'$.entity_id')=? "
        "AND is_active=1", (PRESIDENT_KEY,)).fetchone() is not None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true", help="write (takes a hardened backup first)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    ywcc = _org_node(conn, "ywcc")
    njit_org = conn.execute("SELECT id FROM organizations WHERE slug='njit'").fetchone()
    if ywcc is None or njit_org is None:
        sys.exit("missing ywcc Org node or njit organization — run a gather first")
    njit_org_id = njit_org[0]

    deans = _dean_edges(conn)
    pres_present = _president_exists(conn)

    print("=== plan ===")
    print(f"  Re-point {len(deans)} dean admin appointment(s) college-administration -> ywcc:")
    for eid, name, attrs in deans:
        titles = json.loads(attrs or "{}").get("titles", [])
        print(f"    edge {eid:>4}  {name:<24} {titles}")
    print(f"  Seed President '{PRESIDENT_NAME}' -> NJIT: "
          f"{'already present (skip)' if pres_present else 'CREATE node+edge+KB item'}")

    if not args.commit:
        print("\n(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-dean-repoint-president")
    print(f"\nbackup: {bkp.name}")

    with conn:
        # 1. Option A re-point
        for eid, _name, _attrs in deans:
            conn.execute("UPDATE edges SET dst_id=?, updated_at=datetime('now') WHERE id=?",
                         (ywcc, eid))
        # 2. President — graph (Person node + has_role->NJIT). Idempotent via upsert.
        project_appointment(conn, person_key=PRESIDENT_KEY, name=PRESIDENT_NAME,
                            org_id=njit_org_id, category="admin",
                            titles=[PRESIDENT_TITLE], source_section="President",
                            source="dashboard")
        # 3. President — text layer (knowledge_item under NJIT) for KB tab + RAG. Idempotent.
        if not pres_present:
            meta = json.dumps({"entity_id": PRESIDENT_KEY, "verified": True,
                               "natural_key": f"{PRESIDENT_KEY}:profile:main"})
            # search_text is a GENERATED column (title || ' ' || content) — don't insert it.
            cur = conn.execute(
                "INSERT INTO knowledge_items(org_id,type,title,content,metadata,"
                "version,source_url,is_active,created_by) "
                "VALUES(?,?,?,?,?,1,?,1,'dashboard')",
                (njit_org_id, "profile", PRESIDENT_NAME, PRESIDENT_CONTENT,
                 meta, PRESIDENT_URL))
            conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                         (cur.lastrowid, cur.lastrowid))

    print("committed: deans re-pointed + President seeded.")
    print("next: python v2/scripts/embed_all.py   # embed the new President item")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

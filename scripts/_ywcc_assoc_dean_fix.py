#!/usr/bin/env python3
"""Gated one-off: correct YWCC Associate Deans to match the live college admin page.

Ground truth (computing.njit.edu/administration, confirmed by owner 2026-07-09):
  Associate Deans = Brook Wu + David Bader.

KG was stale:
  - David Bader (people.njit.edu/profile/bader) held only faculty roles -> ADD an
    admin@YWCC "Associate Dean" appointment onto his EXISTING crawler node (never a
    duplicate dashboard node).
  - Guiling Wang (people.njit.edu/profile/gwang) still carried admin@YWCC "Associate
    Dean of Research and External Relations" -> REMOVE that one role (she keeps her
    faculty@CS / joint@Data Science / affiliated@MTSM appointments).

Dry-run by default; --commit takes a hardened_backup then writes live. --db to target a
dev copy first. Producer-durability caveat: the crawler (currently paused) could re-derive
Wang's admin edge from her still-stale profile on a future run; that's the known
multi-source-drift issue, out of scope here.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.core.database.schema import get_connection
from v2.core.graph.project import project_appointment
from v2.core.ingestion.people_editor import remove_person_role
from scripts._area_tag_migrate import hardened_backup

YWCC_ORG_ID = 4
BADER = "people.njit.edu/profile/bader"
WANG = "people.njit.edu/profile/gwang"
YWCC_NODE = 299  # nodes.id of the YWCC Org node (dst of admin@YWCC edges)


def admin_roster(conn):
    return conn.execute(
        """SELECT p.name, json_extract(e.attrs,'$.titles') AS titles
           FROM edges e JOIN nodes p ON p.id=e.src_id
           WHERE e.type='has_role' AND e.category='admin'
             AND e.dst_id=? AND e.is_active=1
           ORDER BY p.name""",
        (YWCC_NODE,),
    ).fetchall()


def show(conn, when):
    print(f"\n--- admin@YWCC roster [{when}] ---")
    for r in admin_roster(conn):
        print(f"  {r['name']:<20} {r['titles']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    conn = get_connection(args.db)
    conn.execute("BEGIN")
    show(conn, "BEFORE")

    # 1) Bader -> admin@YWCC Associate Dean (onto his existing crawler node; additive)
    pid = project_appointment(
        conn, person_key=BADER, name="Bader, David", org_id=YWCC_ORG_ID,
        category="admin", titles=["Distinguished Professor", "Associate Dean"],
        source_section="manual", source="crawler",
    )
    print(f"\n[+] added admin@YWCC 'Associate Dean' to {BADER} (node {pid})")

    # 2) Wang -> drop her admin@YWCC role (keeps faculty/joint/affiliated elsewhere)
    res = remove_person_role(conn, person_key=WANG, org_id=YWCC_ORG_ID)
    print(f"[-] removed admin@YWCC from {WANG}: {res}")

    show(conn, "AFTER")

    if args.commit:
        hardened_backup(args.db, label="ywcc-assoc-dean-fix")
        conn.commit()
        print("\n[COMMITTED] live write done (hardened_backup taken).")
    else:
        conn.rollback()
        print("\n[DRY-RUN] rolled back. Re-run with --commit to write.")
    conn.close()


if __name__ == "__main__":
    main()

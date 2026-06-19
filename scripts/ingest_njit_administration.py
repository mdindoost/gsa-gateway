#!/usr/bin/env python3
"""Seed NJIT Senior Administration (President + cabinet) — gated, source='dashboard'.

njit.edu/about/administration is JS-rendered (names aren't in the static HTML), so the crawler
can't reach the cabinet. This roster was transcribed by the maintainer from that page (2026-06-19).
Same pattern as Theatre/offices: curated, source='dashboard' (M3 never touches it), idempotent.
The President is ALSO appointed at the `njit` (university) root so leadership queries resolve at
the top of the tree. Refresh when the cabinet changes.

Usage: python scripts/ingest_njit_administration.py [--commit]   (default = dry-run)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion.entry_points import apply_org_aliases
from v2.core.ingestion.people_editor import add_or_edit_person

DB_PATH = str(REPO / "gsa_gateway.db")
SRC = "dashboard"

# (name, title, phone) — all category 'admin'. President flagged to also appoint at the njit root.
CABINET = [
    ("Teik C. Lim", "President", "973-596-3101"),
    ("John Pelesko", "Provost and Executive Vice President of Academic Affairs", "973-596-3220"),
    ("Marybeth Boger", "Senior Vice President of Student Affairs and Dean of Students", "973-596-3470"),
    ("William Brady", "Senior Vice President of Human Resources and Institutional Access", "973-596-3138"),
    ("Andrew P. Christ", "Senior Vice President of University Operations", "973-596-5770"),
    ("Sandy A. Curko", "General Counsel, Senior Vice President of Legal Affairs and Secretary to the Board", "973-596-6379"),
    ("Alan J. Kelly", "Senior Vice President of University Advancement", ""),
    ("Stephen Kenney", "Interim Senior Vice President of Finance and Chief Financial Officer", "973-596-3124"),
    ("Michael Johnson", "NJII President", ""),
    ("Lenny Kaplan", "Vice President and Director of Athletics", "973-596-3638"),
    ("Matthew Golden", "Vice President of Communications and Marketing", "973-596-5286"),
    ("Blake Haggerty", "Interim Vice President of Information Services and Technology", "973-596-2912"),
    ("Katie Hageman", "Chief of Staff, Office of the President", "973-596-3104"),
    ("Matthew Bonasia", "Chief of State Government Affairs", "973-596-3328"),
    ("Kim Clark", "Associate Provost and Chief of Staff, Office of the Provost", "973-596-2667"),
    ("Jennifer D'Angelo", "NJII Sr. Vice President and General Manager, Healthcare Division", ""),
    ("Atam Dhawan", "Senior Vice Provost for Research", "973-642-4877"),
    ("Angela Garretson", "Chief of Public & Community Affairs", "973-596-3108"),
    ("David E. Jones", "Chief Campus Culture Officer", "973-596-3050"),
    ("Rebecca Trump", "Senior Associate Vice President and Chief of Staff, University Advancement Alumni", ""),
    ("Susan Gross", "Senior Vice Provost for Enrollment Management", "973-596-3224"),
]
PRESIDENT = "Teik C. Lim"


def _set_phone(conn, person_key, phone):
    if not phone:
        return
    row = conn.execute("SELECT id, attrs FROM nodes WHERE key=?", (person_key,)).fetchone()
    if not row:
        return
    attrs = json.loads(row[1]) if row[1] else {}
    attrs["phone"] = phone
    conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), row[0]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--commit", action="store_true", help="apply (else dry-run)")
    args = ap.parse_args()

    if args.commit:
        print("backup:", hardened_backup(args.db, "pre-njit-administration"))
    conn = get_connection(args.db)
    try:
        admin = ensure_org(conn, "njit-administration", "NJIT Senior Administration",
                           parent_slug="njit", type="unit")
        njit = ensure_org(conn, "njit", "New Jersey Institute of Technology", None, type="university")
        sync_org_nodes(conn)
        apply_org_aliases(conn)
        for name, title, phone in CABINET:
            r = add_or_edit_person(conn, org_id=admin, name=name, title=title,
                                   category="admin", source=SRC)
            _set_phone(conn, r["person_key"], phone)
            print(f"   {name:22} | {title[:58]:60}{(' | '+phone) if phone else ''}")
            if name == PRESIDENT:                       # also at the university root
                rp = add_or_edit_person(conn, org_id=njit, name=name, title=title,
                                        category="admin", source=SRC)
                _set_phone(conn, rp["person_key"], phone)
        if args.commit:
            conn.commit()
            print(f"\n[COMMITTED] {len(CABINET)} senior administrators under njit-administration "
                  f"(+ President also at njit root), source={SRC}.")
        else:
            conn.rollback()
            print(f"\n[DRY-RUN] would add {len(CABINET)} administrators. --commit to apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

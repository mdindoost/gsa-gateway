#!/usr/bin/env python3
"""Seed CSLA's Theater Arts & Technology people (gated, source='dashboard').

Theatre is the one CSLA department whose people page (theatre.njit.edu/our-people) does NOT use
the shared people.njit.edu/profile template — it lists people as name-headings under role
sections — so the crawler can't reach them. They're a small, stable roster, so we curate them
here (same pattern as the offices/GSA non-crawlable sources). source='dashboard' → the crawler's
M3 reconcile never touches them. Idempotent on the person key; re-run to update.

Roster transcribed from theatre.njit.edu/our-people (2026-06-19). Refresh when the page changes.

Usage: python scripts/ingest_theatre_people.py [--commit]   (default = dry-run)
"""
from __future__ import annotations

import argparse
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

# (name, title, category) — category from the page's role section.
THEATRE_PEOPLE = [
    ("Courtney Laine Self", "Director of Theatre Arts and Technology", "admin"),
    ("Rodney Reyes", "Associate Director of Theatre Arts and Technology", "admin"),
    ("Janelle Zapata Castellano", "Administrative Assistant", "staff"),
    ("Raymond Gintner", "Manager of Theatre Operations", "staff"),
    ("Daniel Douress", "Theatre Technician", "staff"),
    ("Emily Edwards", "University Lecturer", "faculty"),
    ("Louis Kornfeld", "Adjunct Professor", "faculty"),
    ("Jonathan Zencheck", "Adjunct Professor", "faculty"),
    ("Stephanie Osin Cohen", "Adjunct Professor", "faculty"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--commit", action="store_true", help="apply (else dry-run)")
    args = ap.parse_args()

    if args.commit:
        print("backup:", hardened_backup(args.db, "pre-theatre-people"))
    conn = get_connection(args.db)
    try:
        # Theatre wasn't a crawler EntryPoint (no profile template), so create its org now.
        oid = ensure_org(conn, "theater-arts-technology", "Theater Arts & Technology",
                         parent_slug="csla", type="department")
        sync_org_nodes(conn)
        apply_org_aliases(conn)        # 'theatre' / 'theater arts' resolve to this org
        for name, title, cat in THEATRE_PEOPLE:
            add_or_edit_person(conn, org_id=oid, name=name, title=title,
                               category=cat, source=SRC)
        if args.commit:
            conn.commit()
            print(f"[COMMITTED] {len(THEATRE_PEOPLE)} Theatre people under "
                  f"theater-arts-technology (org {oid}), source={SRC}.")
        else:
            conn.rollback()
            print(f"[DRY-RUN] would add {len(THEATRE_PEOPLE)} Theatre people. --commit to apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seed NJIT Senior Administration (President + cabinet) — gated, source='dashboard'.

The cabinet is curated here, source='dashboard' (M3 never touches it), idempotent. The
President is ALSO appointed at the `njit` (university) root so leadership queries resolve at
the top of the tree. Refresh when the cabinet changes.

Source: https://www.njit.edu/about/senior-administration (transcribed by the maintainer).
NOTE (2026-08-15): that page IS server-rendered and fetchable — the old comment here claimed
njit.edu/about/administration was JS-rendered, which is no longer true of the
/senior-administration URL. This roster is therefore a candidate for automation; see
`project_full_automation_goal`. Kept manual for now.

Two mechanics this script has to handle explicitly:

  * project_appointment MERGES titles into an existing edge (project.py:143) rather than
    replacing them. Without _replace_titles below, a promotion leaves the OLD title in place
    too — e.g. Pelesko would read "Provost and EVP of Academic Affairs" AND "Interim
    President". Every run therefore rewrites attrs.titles to exactly the listed title.
  * add_or_edit_person only adds/edits, never retires. Departures go in RETIRED and are
    soft-removed via remove_person_role.

Deliberately NOT listed: Moshe Kam (Interim Provost and EVP for Academic Affairs). He already
exists as a CRAWLER node (people.njit.edu/profile/kam) whose NJIT profile carries the new
title, and add_or_edit_person keys on source/org/name — adding him here would mint a SECOND
"Moshe Kam" person. The crawler owns him; a re-crawl picks up his title.

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
from v2.core.ingestion.people_editor import add_or_edit_person, remove_person_role

DB_PATH = str(REPO / "gsa_gateway.db")
SRC = "dashboard"

# (name, title, phone) — all category 'admin'. President flagged to also appoint at the njit root.
# NAMES ARE KEYS: the person key is f"{source}/{org_slug}/{slug(name)}", so changing a spelling
# mints a NEW person. "John Pelesko" is kept verbatim (NOT "John A. Pelesko" as the page now
# renders it) so his existing node is UPDATED rather than duplicated.
CABINET = [
    ("John Pelesko", "Interim President", "973-596-3102"),
    ("Marybeth Boger", "Senior Vice President of Student Affairs and Dean of Students", "973-596-3470"),
    ("William Brady", "Senior Vice President of Human Resources and Institutional Access", "973-596-3138"),
    ("Andrew P. Christ", "Senior Vice President for University Operations", "973-596-5770"),
    ("Sandy A. Curko", "General Counsel, Senior Vice President of Legal Affairs and Secretary to the Board", "973-596-6379"),
    ("Alan J. Kelly", "Senior Vice President of University Advancement", ""),
    ("Stephen Kenney", "Interim Senior Vice President of Finance and Chief Financial Officer", "973-596-3124"),
    ("Michael Johnson", "NJII President", ""),
    ("Lenny Kaplan", "Vice President and Director of Athletics", "973-596-3638"),
    ("Matthew Golden", "Vice President for Communications and Marketing", "973-596-5286"),
    ("Blake Haggerty", "Interim Vice President of Information Services and Technology", "973-596-2912"),
    ("Matthew Bonasia", "Chief of State Government Affairs", "973-596-3328"),
    ("Kim Clark", "Interim Chief of Staff, Office of the President", "973-596-2667"),
    ("Jennifer D'Angelo", "NJII Sr. Vice President and General Manager, Healthcare Division", ""),
    ("Atam Dhawan", "Chief Strategic Innovation Officer", "973-642-4877"),
    ("Angela Garretson", "Chief of Public & Community Affairs", "973-596-3108"),
    ("David E. Jones", "Chief Campus Culture Officer", "973-596-3050"),
    ("Rebecca Trump", "Senior Associate Vice President and Chief of Staff, University Advancement Alumni", ""),
    ("Susan Gross", "Senior Vice Provost for Enrollment Management", "973-596-3224"),
]
PRESIDENT = "John Pelesko"

# Departures — no longer listed on njit.edu/about/senior-administration (checked 2026-08-15).
# (person_key, org_slug). Soft-removed: the has_role edge goes is_active=0, the Person node is
# deactivated if no active role remains, and the bio is retired. Reversible.
RETIRED = [
    ("dashboard/njit-administration/teik-c-lim", "njit-administration"),  # former President
    ("dashboard/njit/teik-c-lim", "njit"),                               # ...and at the root
    ("dashboard/njit-administration/katie-hageman", "njit-administration"),
]


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


def _replace_titles(conn, person_key, org_id, title):
    """Force attrs.titles to EXACTLY [title] on this person's edge to org_id.

    project_appointment merges titles (project.py:143), so a promotion would otherwise leave
    the stale title alongside the new one. Returns the previous titles list for reporting.
    """
    row = conn.execute(
        "SELECT e.id, e.attrs FROM edges e JOIN nodes n ON n.id=e.src_id "
        "WHERE n.key=? AND e.dst_id=(SELECT id FROM nodes WHERE type='Org' "
        "AND json_extract(attrs,'$.org_id')=?) AND e.type='has_role'",
        (person_key, org_id)).fetchone()
    if not row:
        return None
    eid, raw = row
    attrs = json.loads(raw) if raw else {}
    before = attrs.get("titles") or []
    if before == [title]:
        return before
    attrs["titles"] = [title]
    conn.execute("UPDATE edges SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), eid))
    return before


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
        changed = 0
        for name, title, phone in CABINET:
            r = add_or_edit_person(conn, org_id=admin, name=name, title=title,
                                   category="admin", source=SRC)
            _set_phone(conn, r["person_key"], phone)
            before = _replace_titles(conn, r["person_key"], admin, title)
            mark = ""
            if before and before != [title]:
                changed += 1
                mark = f"   <- was: {'; '.join(before)}"
            print(f"   {name:22} | {title[:58]:60}{(' | '+phone) if phone else ''}{mark}")
            if name == PRESIDENT:                       # also at the university root
                rp = add_or_edit_person(conn, org_id=njit, name=name, title=title,
                                        category="admin", source=SRC)
                _set_phone(conn, rp["person_key"], phone)
                _replace_titles(conn, rp["person_key"], njit, title)

        print("\n  Departures:")
        retired = 0
        for person_key, org_slug in RETIRED:
            org = admin if org_slug == "njit-administration" else njit
            res = remove_person_role(conn, person_key=person_key, org_id=org, source=SRC)
            if res.get("removed"):
                retired += 1
            print(f"   {person_key:48} removed={res.get('removed')} "
                  f"person_deactivated={res.get('person_deactivated')}")

        if args.commit:
            conn.commit()
            print(f"\n[COMMITTED] {len(CABINET)} senior administrators under njit-administration "
                  f"(+ President also at njit root), {changed} title(s) rewritten, "
                  f"{retired} role(s) retired. source={SRC}.")
        else:
            conn.rollback()
            print(f"\n[DRY-RUN] would upsert {len(CABINET)} administrators, rewrite {changed} "
                  f"title(s), retire {retired} role(s). --commit to apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

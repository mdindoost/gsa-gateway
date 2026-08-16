#!/usr/bin/env python3
"""Restore college leadership titles the profile-page crawl cannot see — gated, idempotent.

WHY THIS EXISTS (2026-08-15). NJIT restructured people.njit.edu profile pages so a person's
heading carries only their FACULTY RANK ("Distinguished Professor"), while their decanal role is
expressed as a SECTION HEADING on the college site (computing.njit.edu/administration lists
"Associate Deans:" and under it shows Bader as "Distinguished Professor") or only in About-Me
prose (Bandelt: "presently serves as Acting Dean in the Newark College of Engineering").

explore.py reads per-person titles, so the 2026-08-15 re-crawl dropped 33 leadership titles.
Most were REAL turnover and correctly dropped (Nadim -> Golowasch as Bio Sci chair; Guiling Wang
is no longer an associate dean — owner-confirmed). The people below are the ones that are STILL
in post, corroborated against NJIT's own org chart (`njit_orgchart.pdf`, dated 8/5/26 — current:
it shows Pelesko and Kam as Interim) and the college administration pages.

⚠️ MUST BE RE-RUN AFTER EVERY `scripts/run_explore.py`. project_appointment overwrites titles on
the first touch of a crawl run by design ("so a changed title isn't kept stale", project.py:136),
so these titles are wiped by each crawl. This is a merge-back, not a permanent overlay.

Verify each entry against the org chart before editing; do NOT invent titles.

Usage: python scripts/ingest_college_leadership.py [--commit]   (default = dry-run)
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

DB_PATH = str(REPO / "gsa_gateway.db")

# (person_key, org_name, title_to_restore, evidence)
# Titles are restored onto the EXACT edge they occupied before the crawl — a true restore, not a
# re-modelling — except Bandelt, whose role CHANGED (Associate Dean -> Acting Dean).
LEADERSHIP = [
    ("people.njit.edu/profile/bader", "YWCC", "Associate Dean",
     "org chart 'Assoc. Dean Research Dr. David Bader'; computing.njit.edu/administration lists him under 'Associate Deans'; owner-confirmed"),
    ("people.njit.edu/profile/bandelt", "Newark College of Engineering", "Acting Dean",
     "org chart x3 'Acting Dean, Newark College of Engineering Dr. Matthew Bandelt'; profile About-Me prose"),
    ("people.njit.edu/profile/bandelt", "Civil & Environmental Engineering", "Acting Dean",
     "same person, second appointment edge (mirrors pre-crawl title placement)"),
    ("people.njit.edu/profile/sgopalak", "MTSM Administration",
     "Associate Dean for Research and Strategy Initiatives",
     "org chart 'Assoc. Dean Research Dr. Shanthi Gopalakrishnan'; pre-crawl profile title"),
    ("people.njit.edu/profile/sgopalak", "Martin Tuchman School of Management (MTSM)",
     "Associate Dean for Research and Strategy Initiatives", "pre-crawl title placement"),
    ("people.njit.edu/profile/lcumming", "Mathematical Sciences",
     "Associate Dean for Graduate Studies and Research",
     "org chart 'Assoc. Dean Graduate Studies and Research Dr. Linda Cummings'; pre-crawl title"),
    ("people.njit.edu/profile/decker", "Hillier College of Architecture & Design",
     "Associate Dean of Strategic Initiatives",
     "org chart 'Assoc. Dean Strategic Initiatives Prof. Martina Decker'; pre-crawl title"),
    ("people.njit.edu/profile/decker", "School of Art + Design",
     "Associate Dean of Strategic Initiatives", "pre-crawl title placement"),
    ("people.njit.edu/profile/wu", "YWCC", "Associate Dean",
     "org chart 'Assoc. Dean Academic Affairs Dr. Brook Wu'; computing.njit.edu/administration 'Associate Deans'"),
    ("people.njit.edu/profile/wu", "Informatics", "Associate Dean for Academic Affairs",
     "pre-crawl title placement"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--commit", action="store_true", help="apply (else dry-run)")
    args = ap.parse_args()

    if args.commit:
        print("backup:", hardened_backup(args.db, "pre-college-leadership"))
    conn = get_connection(args.db)
    added = skipped = missing = 0
    try:
        for person_key, org_name, title, _why in LEADERSHIP:
            row = conn.execute(
                "SELECT e.id, e.attrs, n.name FROM edges e "
                "JOIN nodes n ON n.id=e.src_id JOIN nodes o ON o.id=e.dst_id "
                "WHERE n.key=? AND o.name=? AND e.type='has_role' AND e.is_active=1",
                (person_key, org_name)).fetchone()
            if not row:
                print(f"  ✗ MISSING edge: {person_key} @ {org_name}")
                missing += 1
                continue
            eid, raw, name = row
            attrs = json.loads(raw) if raw else {}
            titles = attrs.get("titles") or []
            if title in titles:
                print(f"  = {name:26} @ {org_name[:34]:36} already has '{title}'")
                skipped += 1
                continue
            # UNION, preserving the crawler's current rank title — never replace it.
            attrs["titles"] = titles + [title]
            conn.execute("UPDATE edges SET attrs=?, updated_at=datetime('now') WHERE id=?",
                         (json.dumps(attrs), eid))
            print(f"  + {name:26} @ {org_name[:34]:36} {titles} -> {attrs['titles']}")
            added += 1
        if args.commit:
            conn.commit()
            print(f"\n[COMMITTED] {added} title(s) restored, {skipped} already present, "
                  f"{missing} missing edge(s).")
        else:
            conn.rollback()
            print(f"\n[DRY-RUN] would restore {added} title(s); {skipped} already present, "
                  f"{missing} missing. --commit to apply.")
    finally:
        conn.close()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

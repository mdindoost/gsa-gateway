#!/usr/bin/env python3
"""Gated data correction: add the missing 'Department Chair' title to the two YWCC
department chairs the crawler didn't capture.

WHY: NJIT people-profile pages (people.njit.edu/profile/<slug>) — the crawler's source —
do NOT annotate who chairs a department. The chair designation lives only on each
department's own "Administration and Faculty" page. So Data Science and Informatics
rendered with NO Department Chair group, while Computer Science (Vincent Oria, whose
profile DOES list it) rendered correctly.

AUTHORITATIVE SOURCES (department-owned pages, verbatim "Chair"):
  * Data Science  -> James Geller  — https://ds.njit.edu/administration-and-faculty  ("Chair")
  * Informatics   -> Michael Halper — https://informatics.njit.edu/people             ("Chair")

FIX (data-only, no crawler/generator change): append "Department Chair" to each person's
active `has_role` category='faculty' edge to their HOME dept org, so rank_of buckets them
into the leaderboard's Department Chair group and their profile reads "Professor, Department
Chair" (exactly like Oria in CS). Idempotent: skips if the title is already present.

SAFETY: dry-run by default; --commit takes a hardened backup of the live DB first. Only the
one matching active faculty edge per person is touched; nothing else is modified. Mirrors the
Pan Xu / Oria title-in-KG corrections. DB-only -> rebuild FacultyFolio after; no bot restart.

Run:  python scripts/_ywcc_chair_title_fix.py [--db X] [--commit]
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sqlite3
from scripts._area_tag_migrate import hardened_backup

LIVE_DB = os.path.join(ROOT, "gsa_gateway.db")

# (person profile key, home-dept org slug, title to add). Org resolved by slug (stable
# across a --reset renumber), not a bare node id.
FIXES = [
    ("people.njit.edu/profile/geller", "data-science", "Department Chair"),
    ("people.njit.edu/profile/halper", "informatics",  "Department Chair"),
]


def _org_node(conn, slug):
    row = conn.execute(
        """SELECT n.id FROM nodes n JOIN organizations o ON o.id=json_extract(n.attrs,'$.org_id')
           WHERE n.type='Org' AND o.slug=? LIMIT 1""", (slug,)).fetchone()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE_DB)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    if args.commit and os.path.abspath(args.db) == os.path.abspath(LIVE_DB):
        hardened_backup(args.db, "ywcc-chair-title-fix")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    changed = 0
    for person_key, org_slug, title in FIXES:
        org_id = _org_node(conn, org_slug)
        person = conn.execute("SELECT id, name FROM nodes WHERE type='Person' AND key=?",
                              (person_key,)).fetchone()
        if not org_id or not person:
            print(f"SKIP: could not resolve {person_key} @ {org_slug}")
            continue
        edge = conn.execute(
            """SELECT id, attrs FROM edges WHERE src_id=? AND dst_id=? AND type='has_role'
               AND category='faculty' AND is_active=1 ORDER BY id LIMIT 1""",
            (person["id"], org_id)).fetchone()
        if not edge:
            print(f"SKIP: no active faculty edge for {person['name']} @ {org_slug}")
            continue
        attrs = json.loads(edge["attrs"]) if edge["attrs"] else {}
        titles = [t for t in (attrs.get("titles") or []) if t]
        if title in titles:
            print(f"UNCHANGED: {person['name']} @ {org_slug} already has {title!r} -> {titles}")
            continue
        new_titles = titles + [title]
        attrs["titles"] = new_titles
        conn.execute("UPDATE edges SET attrs=? WHERE id=?", (json.dumps(attrs), edge["id"]))
        changed += 1
        print(f"FIX: {person['name']} @ {org_slug}: {titles} -> {new_titles}")

    if args.commit:
        conn.commit()
        print(f"\n✅ COMMITTED {changed} change(s) to {args.db}")
    else:
        conn.rollback()
        print(f"\n(dry-run — {changed} change(s) staged, nothing written. Re-run with --commit.)")
    conn.close()


if __name__ == "__main__":
    main()

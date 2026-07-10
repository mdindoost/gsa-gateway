#!/usr/bin/env python3
"""Gated one-off: shorten Brook Wu's YWCC admin title to just "Associate Dean".

Owner request 2026-07-09: on the college hub her card read "Associate Dean for Academic
Affairs"; owner wants the plain "Associate Dean" (consistency with the other Associate Dean,
David Bader). Surgical edit of her admin@YWCC has_role edge titles only.

Note: her NJIT profile lists the full "Associate Dean for Academic Affairs" verbatim; this
shortens the college-hub label per owner. The crawler (paused) could re-derive the full title
from her profile on a future run — known producer-durability drift.

Dry-run by default; --commit takes a hardened_backup then writes live.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v2.core.database.schema import get_connection
from scripts._area_tag_migrate import hardened_backup

WU = "people.njit.edu/profile/wu"
YWCC_NODE = 299
NEW_TITLES = ["Associate Professor", "Associate Dean"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    conn = get_connection(args.db)
    conn.execute("BEGIN")
    row = conn.execute(
        """SELECT e.id, e.attrs FROM edges e JOIN nodes p ON p.id=e.src_id
           WHERE p.key=? AND e.type='has_role' AND e.category='admin'
             AND e.dst_id=? AND e.is_active=1""",
        (WU, YWCC_NODE),
    ).fetchone()
    if not row:
        print("ERROR: Wu admin@YWCC edge not found")
        return
    eid, attrs_raw = row[0], row[1]
    attrs = json.loads(attrs_raw) if attrs_raw else {}
    print(f"BEFORE: {attrs.get('titles')}")
    attrs["titles"] = NEW_TITLES
    conn.execute("UPDATE edges SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), eid))
    after = conn.execute("SELECT attrs FROM edges WHERE id=?", (eid,)).fetchone()[0]
    print(f"AFTER:  {json.loads(after).get('titles')}")

    if args.commit:
        hardened_backup(args.db, label="wu-assoc-dean-title")
        conn.commit()
        print("[COMMITTED] live write done (hardened_backup taken).")
    else:
        conn.rollback()
        print("[DRY-RUN] rolled back. Re-run with --commit to write.")
    conn.close()


if __name__ == "__main__":
    main()

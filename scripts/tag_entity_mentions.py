"""Gated entity-mentions tagger runner.

Resolves which Person node(s) each in-scope KB item is ABOUT and writes the many-to-many
`entity_mentions` table. Dry-run by default; `--commit` writes after a mandatory
hardened_backup, on a self-owned short-lived writable connection.

Usage:
  python scripts/tag_entity_mentions.py                       # dry-run, prints counts
  python scripts/tag_entity_mentions.py --audit out.csv       # + write an audit CSV
  python scripts/tag_entity_mentions.py --commit --audit out.csv
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.ingestion.entity_mentions import build_mentions, write_mentions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true", help="persist (else dry-run)")
    ap.add_argument("--audit", help="write an audit CSV of every accepted (item, person)")
    ap.add_argument("--roster-n", type=int, default=5, help="anti-roster other-person threshold")
    a = ap.parse_args()

    ro = sqlite3.connect(a.db)
    aw = fh = None
    if a.audit:
        fh = open(a.audit, "w", newline="")
        aw = csv.writer(fh)
        aw.writerow(["item_id", "title", "person", "basis", "confidence"])
    rows = build_mentions(ro, roster_n=a.roster_n, audit_writer=aw)
    if fh:
        fh.close()
    ro.close()

    people = len({r["node_key"] for r in rows})
    print(f"resolved {len(rows)} (item,person) mentions for {people} people"
          + (f"; audit -> {a.audit}" if a.audit else ""))

    if not a.commit:
        print("DRY-RUN — no DB write. Re-run with --commit to persist.")
        return 0

    from scripts._area_tag_migrate import hardened_backup
    hardened_backup(a.db, "entity_mentions")
    w = sqlite3.connect(a.db, timeout=10)
    w.execute("PRAGMA busy_timeout=5000")
    try:
        n = write_mentions(w, rows)
        w.commit()
    finally:
        w.close()
    print(f"COMMITTED {n} mentions to {a.db}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Retire the legacy GSA Q&A (type='faq' under the GSA org) once the KG+KB replaces it.
Items are deactivated (is_active=0), kept for history. Dry-run by default; --commit takes a
hardened backup. Prints the retired titles as a coverage checklist to verify the new KB
answers each."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import sqlite3
from v2.core.database.schema import get_connection
from v2.core.retrieval.skills import resolve_org


def retire_gsa_qa(conn: sqlite3.Connection) -> int:
    """Deactivate active GSA faq items; return the count. NOT committed here."""
    gsa = resolve_org(conn, "gsa")
    if gsa is None:
        return 0
    rows = conn.execute("SELECT id FROM knowledge_items WHERE org_id=? AND type='faq' "
                        "AND is_active=1", (gsa,)).fetchall()
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                     "WHERE id=?", [(r[0],) for r in rows])
    return len(rows)


def main(argv=None) -> int:
    from scripts._area_tag_migrate import hardened_backup
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    conn = get_connection(args.db)
    gsa = resolve_org(conn, "gsa")
    titles = [r[0] for r in conn.execute(
        "SELECT title FROM knowledge_items WHERE org_id=? AND type='faq' AND is_active=1 "
        "ORDER BY title", (gsa,))] if gsa else []
    print(f"GSA QA items to retire: {len(titles)} (coverage checklist)")
    for t in titles:
        print("  -", t)
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-gsa-qa-retire")
    print(f"backup: {bkp.name}")
    with conn:
        n = retire_gsa_qa(conn)
    print(f"committed: retired {n} GSA QA item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

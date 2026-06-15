#!/usr/bin/env python
"""Retire the legacy GSA *officer* contact cards now that officers live in the KG.

The 6 officer cards (GSA President, VP Finances, …) duplicate the officer Person nodes —
their email is carried on the node and surfaced by the officers_in_org answer, and the
shared office/hours live in the 'GSA Office and Contact' KB doc. The 7 *campus-office*
contact cards (Counseling, Library, …) are NOT in the KG, so they are kept.

Match is data-driven: retire GSA-org contact items whose title equals an active GSA
officer's title. Items are deactivated (is_active=0), kept for history. Dry-run by
default; --commit takes a hardened backup first.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.retrieval.skills import officers_in_org, resolve_org


def retire_officer_contacts(conn: sqlite3.Connection) -> list[str]:
    """Deactivate GSA contact items whose title matches a current officer title.
    Returns the retired titles. NOT committed here."""
    gsa = resolve_org(conn, "gsa")
    if gsa is None:
        return []
    officer_titles = {title for _name, title, _email in officers_in_org(conn, gsa)}
    if not officer_titles:
        return []
    rows = conn.execute(
        "SELECT id, title FROM knowledge_items WHERE org_id=? AND type='contact' "
        "AND is_active=1", (gsa,)).fetchall()
    retire = [(r[0], r[1]) for r in rows if r[1] in officer_titles]
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                     "WHERE id=?", [(i,) for i, _ in retire])
    return [t for _, t in retire]


def main(argv=None) -> int:
    from scripts._area_tag_migrate import hardened_backup
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    gsa = resolve_org(conn, "gsa")
    officer_titles = {t for _n, t, _e in officers_in_org(conn, gsa)} if gsa else set()
    candidates = []
    if gsa is not None:
        candidates = [r[0] for r in conn.execute(
            "SELECT title FROM knowledge_items WHERE org_id=? AND type='contact' "
            "AND is_active=1 AND title IN (%s)" % ",".join("?" * len(officer_titles)),
            (gsa, *officer_titles))] if officer_titles else []
    kept = []
    if gsa is not None:
        kept = [r[0] for r in conn.execute(
            "SELECT title FROM knowledge_items WHERE org_id=? AND type='contact' "
            "AND is_active=1", (gsa,)) if r[0] not in officer_titles]
    print(f"officer contact cards to retire ({len(candidates)}): {candidates}")
    print(f"campus-office cards kept ({len(kept)}): {kept}")
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-officer-contacts")
    print(f"backup: {bkp.name}")
    with conn:
        retired = retire_officer_contacts(conn)
    print(f"committed: retired {len(retired)} officer contact card(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

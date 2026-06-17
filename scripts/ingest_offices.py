#!/usr/bin/env python
"""Ingest the NJIT office directory (bot/data/sources/offices/<slug>.md) as contact-type KB
docs, one ORG per office (parent = njit). Mirrors ingest_office_docs.py's safety model but
each office is its own org. Retires the legacy GSA-filed seed contacts it replaces so they
don't duplicate. Dry-run by default; --commit takes a hardened backup, then reminds to embed.
source/created_by='dashboard'.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from scripts.ingest_office_docs import parse_front_matter
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion.gsa_docs import upsert_doc_items

SRC = REPO / "bot" / "data" / "sources" / "offices"

# slug -> (display name, parent slug, type). Add offices here as the pilot grows.
OFFICES: dict[str, tuple[str, str, str]] = {
    "graduate-admissions": ("Office of University Admissions", "njit", "office"),
    "ogi": ("Office of Global Initiatives", "njit", "office"),
    "registrar": ("Office of the Registrar", "njit", "office"),
    "bursar": ("Office of the Bursar / Student Accounts", "njit", "office"),
    "graduate-studies": ("Graduate Studies", "njit", "office"),
    "career-development": ("Career Development Services", "njit", "office"),
    "dean-of-students": ("Dean of Students", "njit", "office"),
    "oars": ("Office of Accessibility Resources & Services", "njit", "office"),
    "counseling": ("Counseling Center (C-CAPS)", "njit", "office"),
    "ist": ("IST / Technology Support", "njit", "office"),
}

# Legacy GSA-filed seed contacts (created_by='migration', under org 2) that we replace.
# Retire these so the new office-filed doc doesn't duplicate them (senior review C1).
LEGACY_SEED: dict[str, int] = {"ogi": 125, "graduate-studies": 122, "counseling": 123}


def ingest_one_office(conn: sqlite3.Connection, *, slug: str, name: str, parent: str,
                      title: str, source_url: str | None, body: str) -> int:
    """Ensure the office org, retire any legacy seed for this slug, then upsert the office
    contact doc. Returns the chunk count. Caller owns the transaction (no commit here)."""
    org_id = ensure_org(conn, slug=slug, name=name, parent_slug=parent, type="office")
    legacy_id = LEGACY_SEED.get(slug)
    if legacy_id is not None:
        conn.execute(
            "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
            "WHERE id=? AND type='contact' AND is_active=1", (legacy_id,))
    return upsert_doc_items(conn, org_id=org_id, slug=slug, title=title,
                            text=body, source_url=source_url, doc_type="contact")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    docs = sorted(SRC.glob("*.md")) if SRC.is_dir() else []
    print(f"found {len(docs)} office doc(s) in {SRC}")
    for d in docs:
        known = "ok" if d.stem in OFFICES else "UNKNOWN SLUG (add to OFFICES)"
        print(f"   {d.name}  [{known}]")
    if not docs:
        return 0
    if any(d.stem not in OFFICES for d in docs):
        sys.exit("some office docs have no OFFICES entry — add them before committing")
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-offices")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    total = 0
    with conn:
        for d in docs:
            name, parent, otype = OFFICES[d.stem]
            title, source_url, sub_body = parse_front_matter(d.read_text(encoding="utf-8"), d.stem)
            total += ingest_one_office(conn, slug=d.stem, name=name, parent=parent,
                                       title=title, source_url=source_url, body=sub_body)
        sync_org_nodes(conn)
    print(f"committed: {total} chunk(s) across {len(docs)} office(s).")
    print("next: python v2/scripts/embed_all.py   then   scripts/_prune_retired_kb_migrate.py --commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

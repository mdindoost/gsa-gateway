#!/usr/bin/env python
"""Gated ingest of crawled + grounded njit-web docs into the KB as source='crawler'.

Reads bot/data/sources/njit-web/*.md (front-matter title+source_url + verbatim grounded facts
that already passed scripts/_crawl_ground_filter.py), files them under the root NJIT org with
doc_type='reference' (retrieved by default) and source='crawler' — so re-crawl/reconcile own
them and they never clobber hand-authored 'dashboard' docs. Dry-run default; --commit takes a
hardened backup first. Embed afterwards with v2/scripts/embed_all.py."""
from __future__ import annotations

import argparse
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

SRC = REPO / "bot" / "data" / "sources" / "njit-web"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    docs = sorted(SRC.glob("*.md"))
    print(f"njit-web: {len(docs)} doc(s) -> org 'njit', source='crawler', doc_type='reference'")
    for d in docs:
        print("   ", d.name)
    if not docs:
        print("no docs")
        return 0
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-crawl-ingest")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    total = 0
    with conn:
        org_id = ensure_org(conn, slug="njit", name="New Jersey Institute of Technology",
                            parent_slug=None, type="university")
        for d in docs:
            title, url, body = parse_front_matter(d.read_text(encoding="utf-8"), d.stem)
            total += upsert_doc_items(conn, org_id=org_id, slug=d.stem, title=title,
                                      text=body, source_url=url, doc_type="reference",
                                      source="crawler")
        sync_org_nodes(conn)
    print(f"committed: {total} chunk(s).")
    print("next: python v2/scripts/embed_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

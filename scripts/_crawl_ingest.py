#!/usr/bin/env python
"""Gated ingest of crawled + grounded njit-web docs into the KB as source='crawler'.

Reads bot/data/sources/njit-web/*.md (front-matter title+source_url + verbatim grounded facts
that already passed scripts/_crawl_ground_filter.py), files them under the root NJIT org by
default — or under a per-doc `org:` front-matter slug (e.g. EOS parking pages → org 'eos') — with
doc_type='reference' (retrieved by default) and source='crawler', so re-crawl/reconcile own them
and they never clobber hand-authored 'dashboard' docs. A per-doc org MUST already exist (a missing
slug is an error, never an auto-guessed org). Dry-run default; --commit takes a hardened backup
first. Embed afterwards with v2/scripts/embed_all.py."""
from __future__ import annotations

import argparse
import re
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

SRC = REPO / "bot" / "data" / "sources" / "njit-web"

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _read_org(text: str) -> str | None:
    """Read an optional `org:` slug from a doc's leading `--- ... ---` front-matter block.
    (Kept separate so the shared parse_front_matter 3-tuple signature is untouched.)"""
    m = _FRONT.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip() == "org":
            return val.strip().strip('"').strip("'") or None
    return None


def _org_id_for(conn: sqlite3.Connection, slug: str) -> int:
    """organizations.id for an EXISTING slug; raise if absent (never auto-create here)."""
    row = conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise ValueError(f"org slug {slug!r} not found — create it before ingesting its docs")
    return row[0]


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
        njit_id = ensure_org(conn, slug="njit", name="New Jersey Institute of Technology",
                             parent_slug=None, type="university")
        for d in docs:
            raw = d.read_text(encoding="utf-8")
            title, url, body = parse_front_matter(raw, d.stem)
            org_slug = _read_org(raw)
            org_id = _org_id_for(conn, org_slug) if org_slug else njit_id
            total += upsert_doc_items(conn, org_id=org_id, slug=d.stem, title=title,
                                      text=body, source_url=url, doc_type="reference",
                                      source="crawler")
        sync_org_nodes(conn)
    print(f"committed: {total} chunk(s).")
    print("next: python v2/scripts/embed_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

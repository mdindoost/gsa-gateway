#!/usr/bin/env python
"""Ingest NJIT office prose docs (Graduate Studies, OGI, ...) into the KB as chunked
knowledge_items, filed under the matching org (created if missing).

Each source folder under bot/data/sources/<office> maps to one organization (see OFFICES).
Markdown files may carry a small YAML front-matter block with `title` and `source_url`;
the front-matter is stripped and the body is chunked. doc slug = filename stem.

Mirrors gsa_ingest_docs.py's safety model: dry-run by default; --commit takes a hardened
backup first, then reminds you to embed. source/created_by='dashboard' (so the crawler
never touches these). Re-running is idempotent — prior chunks for a slug are retired and
re-inserted.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion.gsa_docs import upsert_doc_items

SRC = REPO / "bot" / "data" / "sources"

# source folder -> (org slug, display name, parent slug, org type)
OFFICES: dict[str, tuple[str, str, str, str]] = {
    "graduate-studies": ("graduate-studies", "Graduate Studies", "njit", "office"),
    "ogi": ("ogi", "Office of Global Initiatives", "njit", "office"),
}

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_front_matter(text: str, stem: str) -> tuple[str, str | None, str]:
    """Return (title, source_url, body). Strips an optional leading `--- ... ---` block
    and reads `title`/`source_url`. Falls back to the first H1 (then the stem) for title."""
    title: str | None = None
    source_url: str | None = None
    body = text
    m = _FRONT_MATTER.match(text)
    if m:
        for line in m.group(1).splitlines():
            key, sep, val = line.partition(":")
            if not sep:
                continue
            val = val.strip().strip('"').strip("'")
            if key.strip() == "title":
                title = val or None
            elif key.strip() == "source_url":
                source_url = val or None
        body = text[m.end():]
    if not title:
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        title = h1.group(1).strip() if h1 else stem.replace("-", " ").title()
    return title, source_url, body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    plan: list[tuple[str, str, str, str, list[Path]]] = []
    for folder, (slug, name, parent, otype) in OFFICES.items():
        d = SRC / folder
        docs = sorted([*d.glob("*.md"), *d.glob("*.txt")]) if d.is_dir() else []
        plan.append((slug, name, parent, otype, docs))
        print(f"{folder}: {len(docs)} doc(s) -> org '{slug}' ({name})")
        for doc in docs:
            print("   ", doc.name)

    if not any(docs for *_, docs in plan):
        print("no docs found")
        return 0
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-office-docs")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    total = 0
    with conn:
        for slug, name, parent, otype, docs in plan:
            if not docs:
                continue
            org_id = ensure_org(conn, slug=slug, name=name, parent_slug=parent, type=otype)
            for doc in docs:
                title, source_url, body = parse_front_matter(
                    doc.read_text(encoding="utf-8"), doc.stem)
                total += upsert_doc_items(
                    conn, org_id=org_id, slug=doc.stem, title=title,
                    text=body, source_url=source_url, doc_type="policy")
        sync_org_nodes(conn)
    print(f"committed: {total} chunk(s).")
    print("next: python v2/scripts/embed_all.py   # embed the new chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

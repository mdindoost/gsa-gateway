#!/usr/bin/env python
"""Ingest GSA prose docs (bot/data/sources/gsa/*.md|*.txt) into the KB as chunked
knowledge_items. Dry-run by default; --commit takes a hardened backup, then reminds you to
embed. doc slug = filename stem; title = first markdown H1 or the stem."""
from __future__ import annotations
import argparse, sys, re
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion.gsa_docs import upsert_doc_items
from v2.core.retrieval.skills import resolve_org

SRC = REPO / "bot" / "data" / "sources" / "gsa"


def _title(text: str, stem: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else stem.replace("-", " ").title()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    docs = sorted([*SRC.glob("*.md"), *SRC.glob("*.txt")])
    print(f"found {len(docs)} GSA source doc(s) in {SRC}")
    if not docs:
        return 0
    if not args.commit:
        for d in docs:
            print("  would ingest:", d.name)
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-gsa-docs")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    gsa = resolve_org(conn, "gsa")
    if gsa is None:
        sys.exit("no GSA org — run gsa_ingest_people.py --commit first")
    total = 0
    with conn:
        for d in docs:
            text = d.read_text(encoding="utf-8")
            total += upsert_doc_items(conn, org_id=gsa, slug=d.stem, title=_title(text, d.stem),
                                      text=text, source_url=None, doc_type="policy")
    print(f"committed: {total} chunk(s) across {len(docs)} doc(s).")
    print("next: python v2/scripts/embed_all.py   # embed the new GSA chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

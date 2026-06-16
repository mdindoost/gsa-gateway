#!/usr/bin/env python
"""One-off gated cleanup: hard-delete retired (is_active=0) knowledge_items and their
orphaned vectors.

Retired rows accumulate because every re-run of an idempotent doc ingester
(ingest_office_docs.py / gsa_ingest_docs.py) retires the prior chunks for a slug and
re-inserts fresh ones. They never appear in answers (retrieval filters is_active=1) but
they carry dead vectors and FTS entries. This prunes them.

Safety (mirrors the other _*_migrate.py scripts): dry-run by default; --commit takes a
hardened backup first. The FTS5 external-content index is kept in sync automatically by
the knowledge_items AFTER DELETE trigger; we delete the matching knowledge_vectors rows
explicitly (vec0 has no trigger). Only rows with NO active referrer are touched (verified
in the dry run), so version chains for live items are never broken.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)

    by_src = conn.execute(
        "SELECT created_by, COUNT(*) n FROM knowledge_items WHERE is_active=0 "
        "GROUP BY created_by ORDER BY n DESC").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=0").fetchone()[0]
    vecs = conn.execute(
        "SELECT COUNT(*) FROM knowledge_vectors WHERE item_id IN "
        "(SELECT id FROM knowledge_items WHERE is_active=0)").fetchone()[0]
    refs = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items a WHERE a.is_active=1 AND ("
        "a.root_id   IN (SELECT id FROM knowledge_items WHERE is_active=0) OR "
        "a.parent_id IN (SELECT id FROM knowledge_items WHERE is_active=0))").fetchone()[0]

    print("retired knowledge_items to delete:")
    for r in by_src:
        print(f"   {r['created_by']:<12} {r['n']}")
    print(f"   TOTAL {total}  (+ {vecs} orphaned vectors)")
    print(f"active rows referencing a retired row (must be 0): {refs}")

    if refs:
        print("ABORT: some active rows still reference retired rows — not safe to delete.")
        return 1
    if total == 0:
        print("nothing to prune.")
        return 0
    if not args.commit:
        print("(dry run — pass --commit to delete; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-prune-retired-kb")
    print(f"backup: {bkp.name}")
    with conn:
        conn.execute(
            "DELETE FROM knowledge_vectors WHERE item_id IN "
            "(SELECT id FROM knowledge_items WHERE is_active=0)")
        # AFTER DELETE trigger (knowledge_items_fts_ad) keeps the FTS5 index in sync.
        deleted = conn.execute("DELETE FROM knowledge_items WHERE is_active=0").rowcount
    print(f"deleted {deleted} retired item(s) + {vecs} vector(s).")
    remaining = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=0").fetchone()[0]
    print(f"remaining retired rows: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

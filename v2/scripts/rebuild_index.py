"""Rebuild the FTS index and embed any knowledge_items missing vectors.

The dashboard authors KB content without FTS5 (sql.js has none) and without
Ollama (browser). Run this afterwards to make new/edited content searchable by
the bot:

  1. ensure schema + triggers (create_all is idempotent — recreates the FTS sync
     triggers the dashboard drops in its in-memory copy)
  2. rebuild knowledge_fts from knowledge_items (FTS5 'rebuild' command)
  3. embed every active knowledge_item that has no vector yet, via Ollama

Usage:
    python v2/scripts/rebuild_index.py              # FTS rebuild + embed missing
    python v2/scripts/rebuild_index.py --fts-only    # FTS rebuild only
    python v2/scripts/rebuild_index.py --embed-only   # embed missing only
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all, get_connection
from v2.scripts.embed_all import (
    GREEN, RED, RESET, YELLOW, _store_vector, embed_document, health_check,
)

LIVE_DB = str(REPO_ROOT / "gsa_gateway.db")


def rebuild_fts(conn) -> int:
    """Rebuild the external-content FTS5 index from knowledge_items."""
    conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1").fetchone()[0]


def missing_vectors(conn):
    # Native connection has sqlite-vec loaded, so query the vec0 table directly.
    return conn.execute(
        "SELECT ki.id, ki.type, ki.title, ki.search_text FROM knowledge_items ki "
        "WHERE ki.is_active=1 AND ki.id NOT IN (SELECT item_id FROM knowledge_vectors) "
        "ORDER BY ki.id"
    ).fetchall()


def embed_missing(conn):
    targets = missing_vectors(conn)
    n = len(targets)
    if n == 0:
        print("  No items missing embeddings.")
        return 0, []
    print(f"  Embedding {n} new item(s):")
    succeeded, failed = 0, []
    for i, row in enumerate(targets, 1):
        title = (row["title"] or row["search_text"] or "").strip().replace("\n", " ")
        vec = None
        for _ in (1, 2):  # try once, retry once
            try:
                vec = embed_document(row["search_text"])
                if vec is not None:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        if vec is not None and _store_vector(conn, row["id"], vec):
            succeeded += 1
            print(f"    [{i}/{n}] {row['type']}: {title[:48]} {GREEN}✅{RESET}")
        else:
            failed.append((row["id"], row["type"], title[:48]))
            print(f"    [{i}/{n}] {row['type']}: {title[:48]} {RED}✗ skipped{RESET}")
        time.sleep(0.05)
    conn.commit()
    return succeeded, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebuild FTS + embed missing knowledge_items.")
    ap.add_argument("db_path", nargs="?", default=LIVE_DB, help="Target db (default: live gsa_gateway.db)")
    ap.add_argument("--fts-only", action="store_true", help="Rebuild FTS only.")
    ap.add_argument("--embed-only", action="store_true", help="Embed missing items only.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db_path):
        ap.error(f"Database not found: {args.db_path}")

    do_fts = not args.embed_only
    do_embed = not args.fts_only

    if do_embed:
        health_check()  # verify Ollama before touching the db

    # Recreate any triggers the dashboard dropped (idempotent).
    create_all(args.db_path).close()
    conn = get_connection(args.db_path)
    try:
        fts_count = 0
        succeeded, failed = 0, []
        if do_fts:
            fts_count = rebuild_fts(conn)
            print(f"FTS rebuilt: {fts_count} items indexed")
        if do_embed:
            succeeded, failed = embed_missing(conn)

        total = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0]
        ok = embedded >= total
        bar = "═" * 46
        print("\n" + bar)
        if do_fts:
            print(f"  FTS rebuilt:     {fts_count} items indexed")
        if do_embed:
            print(f"  New embeddings:  {succeeded} item(s)")
            if failed:
                print(f"  Failed:          {len(failed)} -> {[f[0] for f in failed]}")
        mark = f"{GREEN}✅{RESET}" if ok else f"{YELLOW}⚠ incomplete{RESET}"
        print(f"  Total coverage:  {embedded}/{total} {mark}")
        print(bar + "\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

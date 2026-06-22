#!/usr/bin/env python
"""Recurring gated re-crawl of office entry points whose crawl_interval has elapsed. Reuses
harvest_entry_point (crawl → change-detected ingest → 404 retire → candidate discovery). Dry-run
default; --commit takes a hardened backup. Embed afterwards. spec §4.5/§4.6 Plan C."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from scripts.harvest_office import harvest_entry_point
from v2.core.database.schema import get_connection
from v2.core.ingestion.web_crawler import fetch_with_status


def due_entry_points(conn, now=None):
    """Active office entry points due for re-crawl: never crawled, or last_crawled_at older than
    crawl_interval_days. Entry points with a NULL interval are excluded (no recurrence configured)."""
    return conn.execute(
        "SELECT * FROM crawl_entry_points WHERE status='active' AND aspect='office' "
        "AND crawl_interval_days IS NOT NULL "
        "AND (last_crawled_at IS NULL OR "
        "     julianday(?) - julianday(last_crawled_at) >= crawl_interval_days) "
        "ORDER BY id", (now or "now",)).fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--budget", type=int, default=60)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    rows = due_entry_points(conn)
    print(f"recrawl: {len(rows)} entry point(s) due")
    for r in rows:
        print("   ", r["url"])
    if not rows:
        return 0
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-recrawl")
    print(f"backup: {bkp.name}")
    fetch = fetch_with_status()
    with conn:
        for r in rows:
            print(f"  {r['url']}: {harvest_entry_point(conn, r, fetch, budget=args.budget, depth=args.depth)}")
    print("next: python v2/scripts/embed_all.py  (then review staged high-stakes pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Gated runner for the college/department PROSE crawler (Crawling 2.1).

Dry-run by default; --commit writes the live DB (hardened_backup first). --entry <slug> runs one
entry point (independent recrawl); default runs all PROSE_ENTRY_POINTS. People are crawled
separately via scripts/run_explore.py (explore.py owns people) — a full YWCC refresh = both.

Gated workflow:  cp gsa_gateway.db /tmp/dev.db
                 python scripts/crawl_college.py --db /tmp/dev.db            # dev dry-run
                 python scripts/crawl_college.py --db /tmp/dev.db --commit   # dev write, inspect
                 python scripts/crawl_college.py --commit --embed             # live

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.ingestion.college_crawl import (
    PROSE_ENTRY_POINTS, extract_entry, ingest_college, ProseEntry)
from v2.core.ingestion.web_crawler import make_fetcher


def run_entry(conn, entry: ProseEntry, fetch, max_depth=4, budget=400, delay=0.3) -> dict:
    """Extract prose from one entry point and ingest into knowledge_items. No commit (caller owns
    the transaction). Returns a summary dict with prose_inserted/updated/unchanged/skipped."""
    res = extract_entry(entry.seed, fetch, max_depth=max_depth, budget=budget, delay=delay)
    out = ingest_college(conn, entry.org_slug, entry.org_name, entry.parent_slug,
                         res, res.html_by_url)
    out.update(entry=entry.org_slug, truncated=res.truncated)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="College prose crawler (Crawling 2.1)")
    ap.add_argument("--db", default="gsa_gateway.db", help="Path to the SQLite database")
    ap.add_argument("--entry", help="org_slug of one PROSE_ENTRY_POINTS member; default = all")
    ap.add_argument("--budget", type=int, default=400, help="Max pages per entry point")
    ap.add_argument("--delay", type=float, default=0.3, help="Politeness delay between fetches (s)")
    ap.add_argument("--commit", action="store_true", help="Write to the live DB (hardened_backup first)")
    ap.add_argument("--embed", action="store_true", help="Run embed_all.py after commit")
    args = ap.parse_args(argv)

    entries = [e for e in PROSE_ENTRY_POINTS if not args.entry or e.org_slug == args.entry]
    if not entries:
        print(f"ERROR: no PROSE_ENTRY_POINTS member with org_slug={args.entry!r}")
        sys.exit(2)

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="college-crawl")

    conn = get_connection(args.db)
    fetch = make_fetcher()
    totals = []
    for e in entries:
        print(f"crawling: {e.org_slug} ({e.seed})")
        out = run_entry(conn, e, fetch, budget=args.budget, delay=args.delay)
        totals.append(out)
        print(out)

    if args.commit:
        conn.commit()
        print("COMMITTED")
        if args.embed:
            import subprocess
            subprocess.run([sys.executable, "v2/scripts/embed_all.py"], check=True)
    else:
        print("DRY RUN — no commit (use --commit to write)")

    return totals


if __name__ == "__main__":
    main()

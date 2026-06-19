#!/usr/bin/env python3
"""Refresh Google Scholar metrics for people who have a Scholar profile URL.

Gated: dry-run by default (lists who WOULD be refreshed); --commit takes a hardened backup,
fetches + updates metrics, and commits. Provider note: the default fetch is best-effort urllib
and Scholar blocks bots — for a full refresh swap a sanctioned provider into scholar.default_fetch.

  python scripts/refresh_scholar.py                      # dry-run: list targets
  python scripts/refresh_scholar.py --commit             # backup + refresh all (polite delay)
  python scripts/refresh_scholar.py --key <person_key> --commit
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
from v2.core.ingestion import scholar


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--key", help="only this person key")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between fetches (be polite)")
    ap.add_argument("--commit", action="store_true", help="actually fetch + write (else dry-run)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    targets = scholar.people_with_scholar(conn)
    if args.key:
        targets = [(k, u) for k, u in targets if k == args.key]
    print(f"{len(targets)} person(s) with a Scholar URL"
          + (f" (filtered to {args.key})" if args.key else ""))
    for k, u in targets[:50]:
        print(f"  {k}  {u}")

    if not args.commit:
        print("\nDRY-RUN. Re-run with --commit to fetch metrics and write.")
        return 0
    if not targets:
        print("Nothing to do.")
        return 0

    print(f"\nBackup: {hardened_backup(args.db, 'pre-scholar-refresh')}")
    out = scholar.refresh_scholar(conn, only_key=args.key, delay=args.delay)
    conn.commit()
    print(f"\nDone: {out['updated']} updated, {out['failed']} failed of {out['people']}.")
    for key, why in out["errors"][:20]:
        print(f"  ✗ {key}: {why}")
    if out["failed"]:
        print("\n(Failures are expected from raw Scholar scraping — swap a sanctioned provider "
              "into scholar.default_fetch for a reliable full refresh.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

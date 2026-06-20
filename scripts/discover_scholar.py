#!/usr/bin/env python3
"""Discover + add Google Scholar URLs for faculty who lack one (search + verified-njit gate).

Gated: dry-run by default (LISTS the faculty who WOULD be searched — no Brave spend); --commit
takes a hardened backup, searches Brave per person, AUTO-WRITES only strict matches (verified
njit.edu email + name match + unique-surname/corroboration), queues uncertain to a review CSV,
and never guesses. See docs/superpowers/specs/2026-06-20-scholar-url-discovery-design.md

  python scripts/discover_scholar.py --org nce                      # dry-run: who would be searched
  python scripts/discover_scholar.py --org nce --limit 50 --commit --embed
"""
from __future__ import annotations

import argparse
import csv
import datetime
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion import scholar_discovery as D
from v2.integration.njit_search import web_search
from v2.core.ingestion.scholar import default_fetch


def _embed_cmd(db_path: str) -> list[str]:
    return [sys.executable, str(REPO / "v2" / "scripts" / "embed_all.py"), str(db_path)]


def _write_review_csv(scope: str, queue: list) -> Path:
    logs = REPO / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"scholar_review_{scope or 'all'}_{datetime.date.today():%Y%m%d}.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person_key", "name", "candidate_url", "reason"])
        w.writerows(queue)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--org", "--department", dest="org", help="scope to an org slug (subtree)")
    ap.add_argument("--limit", type=int, default=50, help="cap targets per run (Brave budget)")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between people (be polite)")
    ap.add_argument("--embed", action="store_true", help="embed new research-area items after commit")
    ap.add_argument("--commit", action="store_true", help="actually search + write (else dry-run)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    targets = D.select_discovery_targets(conn, org_scope=args.org, limit=args.limit)
    print(f"{len(targets)} faculty without a Scholar URL would be searched "
          f"(scope: {args.org or 'all'}, limit {args.limit})")
    for _, name in targets[:50]:
        print(f"  {name}")

    if not args.commit:
        print("\nDRY-RUN. Re-run with --commit to search Brave + write strict matches.")
        return 0
    if not targets:
        print("Nothing to do.")
        return 0

    print(f"\nBackup: {hardened_backup(args.db, 'pre-scholar-discovery')}")
    stats = D.run(conn, web_search=web_search, fetch=default_fetch,
                  org_scope=args.org, limit=args.limit, delay=args.delay)
    conn.commit()
    csv_path = _write_review_csv(args.org or "all", stats["queue"])
    # Recognized completion line for the dashboard job summarizer (jobs._summarize).
    print(f"\nScholar discovery complete: {stats['written']} written, {stats['queued']} queued "
          f"of {stats['scanned']} (blocked {stats['blocked']}, {stats['brave_calls']} Brave calls).")
    print(f"Review queue ({stats['queued']}): {csv_path}")
    if args.embed and stats["written"]:
        print("\nEmbedding new research-area items…")
        try:
            subprocess.run(_embed_cmd(args.db), cwd=str(REPO))
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ embed failed ({exc}) — data is committed; run embed_all when Ollama is up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Refresh Google Scholar metrics for people who have a Scholar profile URL.

Gated: dry-run by default (lists who WOULD be refreshed); --commit takes a hardened backup,
fetches + updates metrics, and commits. Provider note: the default fetch is best-effort urllib
and Scholar blocks bots — for a full refresh swap a sanctioned provider into scholar.default_fetch.

  python scripts/refresh_scholar.py                      # dry-run: list targets
  python scripts/refresh_scholar.py --commit             # backup + refresh all (polite delay)
  python scripts/refresh_scholar.py --key <person_key> --commit
  python scripts/refresh_scholar.py --org ywcc --older-than 30 --commit --embed
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion import scholar


def _embed_cmd(db_path: str) -> list[str]:
    """The embed command. embed_all.py takes db_path POSITIONALLY (not --db)."""
    return [sys.executable, str(REPO / "v2" / "scripts" / "embed_all.py"), str(db_path)]


def _run_embed(db_path: str) -> bool:
    """Shell out to the resumable embedder (only NEW research-area items get vectors). Embed
    failure (e.g. Ollama down) must NOT undo the already-committed metrics/areas write."""
    try:
        return subprocess.run(_embed_cmd(db_path), cwd=str(REPO)).returncode == 0
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ embed failed ({exc}) — data is committed; run embed_all when Ollama is up.")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--key", help="only this person key")
    ap.add_argument("--org", "--department", dest="org", help="scope to an org slug (college or department; includes its subtree)")
    ap.add_argument("--older-than", dest="older_than", type=int, default=None,
                    help="only refresh profiles whose scholar.updated_at is older than N days")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between fetches (be polite)")
    ap.add_argument("--embed", action="store_true", help="embed new research-area items after a successful commit")
    ap.add_argument("--commit", action="store_true", help="actually fetch + write (else dry-run)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    keys = scholar.select_scholar_targets(conn, org_scope=args.org, older_than_days=args.older_than)
    if args.key:
        keys = [k for k in keys if k == args.key]
    scope_desc = ", ".join(filter(None, [
        f"org={args.org}" if args.org else None,
        f"older-than={args.older_than}d" if args.older_than is not None else None,
        f"key={args.key}" if args.key else None])) or "all"
    print(f"{len(keys)} person(s) to refresh (scope: {scope_desc})")
    for k in keys[:50]:
        print(f"  {k}")

    if not args.commit:
        print("\nDRY-RUN. Re-run with --commit to fetch metrics and write.")
        return 0
    if not keys:
        print("Nothing to do.")
        return 0

    print(f"\nBackup: {hardened_backup(args.db, 'pre-scholar-refresh')}")
    out = scholar.refresh_scholar(conn, only_keys=set(keys), delay=args.delay)
    conn.commit()
    # Recognized completion line for the dashboard job summarizer (jobs._summarize).
    print(f"\nScholar refresh complete: {out['updated']} updated, {out['areas_updated']} areas, "
          f"{out['failed']} failed of {out['people']}.")
    for key, why in out["errors"][:20]:
        print(f"  ✗ {key}: {why}")
    if out["failed"]:
        print("\n(Failures are expected from raw Scholar scraping — swap a sanctioned provider "
              "into scholar.default_fetch for a reliable full refresh.)")
    if args.embed:
        print("\nEmbedding new research-area items…")
        _run_embed(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

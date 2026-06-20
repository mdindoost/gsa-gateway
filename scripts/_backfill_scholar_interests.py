"""One-off gated backfill: capture Google Scholar research INTERESTS for every person who already
has a Scholar URL, turning them into KG research areas (source='scholar') via the S6-wired
refresh_scholar. Also refreshes their metrics. Per-person commit so the live bots' WAL writes are
never blocked by a long transaction. Dry-run by default; --commit to write (hardened_backup first).

    python scripts/_backfill_scholar_interests.py            # dry-run (lists targets)
    python scripts/_backfill_scholar_interests.py --commit   # write, then run embed_all
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.ingestion.scholar import people_with_scholar, refresh_scholar
from scripts._area_tag_migrate import hardened_backup

DB = "gsa_gateway.db"
SLEEP = 2.0  # between people — be polite to Scholar


def main(commit: bool) -> None:
    conn = get_connection(DB)
    targets = people_with_scholar(conn)
    print(f"{len(targets)} people with a Scholar URL")
    if not commit:
        for k, u in targets[:10]:
            print("  ", k, u)
        print("… dry-run; pass --commit to backfill metrics + interests.")
        conn.close()
        return
    conn.close()

    hardened_backup(DB, "backfill-scholar-interests")
    totals = {"updated": 0, "areas_updated": 0, "failed": 0}
    fails = []
    for i, (key, _url) in enumerate(targets):
        conn = get_connection(DB)            # short txn per person → releases the write lock
        try:
            s = refresh_scholar(conn, only_key=key, delay=0)
            conn.commit()
        finally:
            conn.close()
        totals["updated"] += s["updated"]
        totals["areas_updated"] += s["areas_updated"]
        totals["failed"] += s["failed"]
        fails += s["errors"]
        if (i + 1) % 10 == 0 or i + 1 == len(targets):
            print(f"  [{i+1}/{len(targets)}] metrics={totals['updated']} "
                  f"areas={totals['areas_updated']} failed={totals['failed']}")
        time.sleep(SLEEP)
    print("DONE:", totals)
    if fails:
        print("failures (key, reason):")
        for f in fails:
            print("  ", f)


if __name__ == "__main__":
    main("--commit" in sys.argv)

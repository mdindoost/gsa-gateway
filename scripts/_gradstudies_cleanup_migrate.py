#!/usr/bin/env python3
"""Clean-replace the pre-crawler Graduate Studies KB rows (SEPARATE gated migration).

Run ONLY after crawl_gradstudies.py --commit has written + verified the new crawler rows.
Retires: the dead-subdomain migration stub, the superseded njit-crawl rows, and duplicate
dashboard rows (keeps the lowest-id one). KEEPS: any dashboard row whose source_url is NOT a
live www.njit.edu/graduatestudies page (genuinely manual) and every crawler row. This is a
CURATION decision — it lives OUTSIDE the crawler (hard line: the crawler brings data only).
Source-scoped + dry-run default + hardened backup before any write.

Spec: docs/superpowers/specs/2026-06-24-graduate-studies-crawl-design.md (G7)
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

GS_SLUG = "graduate-studies"


def select_retire(conn) -> list[dict]:
    """Rows to deactivate so GSO ends with ONE clean crawler source:
      - every ``migration`` row (the dead graduatestudies.njit.edu subdomain stub);
      - every ``njit-crawl`` row (the older one-off pass, now superseded);
      - every ``dashboard`` row whose ``source_url`` is a live /graduatestudies/ page the
        crawler now covers (a whole-page crawler row exists for that URL) — these are
        paraphrased/chunked versions of pages we now hold verbatim, NOT manual-only.
    KEPT: every ``crawler`` row, and any ``dashboard`` row with NO crawler overlap (genuinely
    manual — e.g. an internally-authored note or a row with no live-site URL incl. NULL).
    This is a CURATION decision — it lives OUTSIDE the crawler (hard line)."""
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (GS_SLUG,)).fetchone()[0]
    crawler_urls = {r[0] for r in conn.execute(
        "SELECT DISTINCT source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=? AND created_by='crawler'", (oid,)) if r[0]}
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()
    retire: list[dict] = []
    for rid, cb, title, url in rows:
        if cb == "crawler":
            continue                                     # the new source of truth — never retired
        if cb == "migration":
            reason = ("dead-subdomain-stub" if url and "graduatestudies.njit.edu" in url
                      else "superseded-by-crawler")
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": reason})
        elif cb == "njit-crawl":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard" and url in crawler_urls:
            # a dashboard row on a page the crawler now holds verbatim → not manual-only
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        # dashboard rows NOT on a crawler-covered URL (incl. NULL url) are manual-only → KEPT
    return retire


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="deactivate the rows (else dry run)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    retire = select_retire(conn)
    for r in retire:
        print(f"  retire id={r['id']:>6} [{r['created_by']}] {r['reason']:<24} {r['source_url']}")
    print(f"=== {len(retire)} rows to retire ===")

    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0

    hardened_backup(args.db, "pre-gradstudies-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    conn.commit()
    print(f"RETIRED {len(retire)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

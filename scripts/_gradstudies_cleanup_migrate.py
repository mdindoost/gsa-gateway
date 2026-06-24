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
    """Rows to deactivate. migration + njit-crawl rows are superseded by the crawler; duplicate
    dashboard rows (same source_url) collapse to the lowest id. crawler rows and manual-only
    dashboard rows are never touched."""
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (GS_SLUG,)).fetchone()[0]
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()
    retire: list[dict] = []
    dash_by_url: dict[str, list[int]] = {}
    for rid, cb, title, url in rows:
        if cb == "migration":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "dead-subdomain-stub"})
        elif cb == "njit-crawl":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard":
            dash_by_url.setdefault(url, []).append(rid)
        # crawler rows are NEVER retired here
    # dedup dashboard rows sharing a source_url: keep the lowest id, retire the rest
    for url, ids in dash_by_url.items():
        for rid in sorted(ids)[1:]:
            retire.append({"id": rid, "created_by": "dashboard", "title": "(duplicate)",
                           "source_url": url, "reason": "duplicate-dashboard-row"})
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

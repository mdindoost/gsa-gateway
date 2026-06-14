#!/usr/bin/env python
"""One-time backfill: populate metadata.areas on existing active research_areas items.

Lossless — decompose joins areas with "; " and areas never contain ',' or ';', so the
content tail recovers the exact list. DEFAULT IS A DRY RUN; --commit writes (auto-backup
first). Going forward decompose writes metadata.areas natively.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def area_tags_from_content(content: str) -> list[str]:
    """Recover the discrete area list from a 'Research areas of X: A; B; C' string."""
    if ": " not in content:
        return []
    tail = content.split(": ", 1)[1]
    return [a.strip() for a in tail.split("; ") if a.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true", help="write changes (else dry run)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, content, metadata FROM knowledge_items "
        "WHERE type='research_areas' AND is_active=1").fetchall()

    planned = []
    for r in rows:
        meta = json.loads(r["metadata"])
        if meta.get("areas"):
            continue  # already has it (native decompose)
        tags = area_tags_from_content(r["content"])
        if tags:
            meta["areas"] = tags
            planned.append((r["id"], json.dumps(meta), tags))

    print(f"{len(rows)} active research_areas items; {len(planned)} need backfill.")
    for _id, _m, tags in planned[:5]:
        print(f"  id={_id}: {tags}")

    if not args.commit:
        print("DRY RUN — pass --commit to write.")
        return 0

    backup = REPO / ".backups" / f"gsa_gateway.{datetime.now():%Y%m%d-%H%M%S}.pre-areas-backfill.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(args.db, backup)
    print(f"backup: {backup}")
    conn.executemany("UPDATE knowledge_items SET metadata=? WHERE id=?",
                     [(m, i) for i, m, _t in planned])
    conn.commit()
    print(f"backfilled {len(planned)} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

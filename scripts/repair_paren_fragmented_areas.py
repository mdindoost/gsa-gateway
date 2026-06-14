#!/usr/bin/env python
"""One-time repair: regroup research_areas tags that were fragmented on commas inside
parentheses by the pre-2026-06-14 splitter (e.g. 'Machine Learning (Statistical Learning'
+ 'Kernel Methods' + 'Similarity Measures)' → one area).

The source bug is fixed in njit_adapter._split_areas (now paren-aware), so this only
repairs existing rows; future ingests are correct. Each affected row's metadata.areas is
re-derived by running the fixed splitter on the stored content tail — the canonical path,
so a verbose area that trips the prose/single-token filters honestly collapses to [].

DEFAULT IS A DRY RUN; --commit writes (auto-backup first).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO))

from v2.core.ingestion.njit_adapter import _split_areas


def has_unbalanced(areas: list[str]) -> bool:
    """True if any area has mismatched parens — the fingerprint of comma-in-paren
    fragmentation (a real area always has balanced parens)."""
    return any(a.count("(") != a.count(")") for a in areas)


def rederive_areas(content: str) -> list[str]:
    """Re-run the (now paren-aware) splitter on the stored 'Research areas of X: ...' tail."""
    if ": " not in content:
        return []
    return _split_areas(content.split(": ", 1)[1])


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
        areas = meta.get("areas") or []
        if not has_unbalanced(areas):
            continue
        new_areas = rederive_areas(r["content"])
        meta["areas"] = new_areas
        planned.append((r["id"], json.dumps(meta), areas, new_areas))

    print(f"{len(rows)} active research_areas items; {len(planned)} fragmented, will repair.")
    for _id, _m, old, new in planned:
        print(f"  id={_id}:\n    OLD {old}\n    NEW {new}")

    if not args.commit:
        print("DRY RUN — pass --commit to write.")
        return 0

    backup = REPO / ".backups" / f"gsa_gateway.{datetime.now():%Y%m%d-%H%M%S}.pre-paren-repair.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(args.db, backup)
    print(f"backup: {backup}")
    conn.executemany("UPDATE knowledge_items SET metadata=? WHERE id=?",
                     [(m, i) for i, m, _o, _n in planned])
    conn.commit()
    print(f"repaired {len(planned)} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

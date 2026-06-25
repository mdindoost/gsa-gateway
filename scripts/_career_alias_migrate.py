"""One-off gated migration: add name aliases to the Career Development org (id 18).

Org 20 slug is `dean-of-students` and name "Dean of Students". This adds the acronym + office phrasings
("dos", "office of the dean of students") so those
resolve to it. Idempotent; dry-run by default; --commit writes behind a mandatory hardened_backup.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup

SLUG = "career-development"
NEW_ALIASES = ["career services", "career development", "career development services", "cds"]


def run(db: str, commit: bool) -> int:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT id, name, metadata FROM organizations WHERE slug=?", (SLUG,)).fetchone()
        if not row:
            print(f"ERROR: no org with slug '{SLUG}'"); return 1
        oid, name, metadata = row
        meta = json.loads(metadata) if metadata else {}
        existing = [str(a).strip() for a in (meta.get("aliases") or [])]
        existing_lc = {a.lower() for a in existing}
        to_add = [a for a in NEW_ALIASES if a.lower() not in existing_lc]
        merged = existing + to_add
        print(f"Org #{oid}  {name}  (slug={SLUG})")
        print(f"  current aliases: {existing or '[]'}")
        print(f"  adding:          {to_add or '[] (already present)'}")
        print(f"  result aliases:  {merged}")
        if not to_add:
            print("\nNothing to do — all aliases already present."); return 0
        if not commit:
            print("\nDRY-RUN — pass --commit to write."); return 0
        hardened_backup(db, label="career-alias")
        meta["aliases"] = merged
        conn.execute("UPDATE organizations SET metadata=? WHERE id=?", (json.dumps(meta), oid))
        conn.commit()
        print("\nCOMMITTED."); return 0
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    raise SystemExit(run(args.db, args.commit))

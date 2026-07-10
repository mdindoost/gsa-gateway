#!/usr/bin/env python3
"""One-off gated write: add Jamie Payton's (js2852) Ph.D. education KB item.

Her NJIT profile page is a stub with no education section, so the crawler never
produced a `type='education'` item and her FacultyFolio Education panel is empty.
Her degree is verifiable but only her Ph.D. has a confirmed year (LinkedIn:
Ph.D. Computer Science, Washington University in St. Louis, conferred 2006).
We add ONLY that one degree (honest-partial — B.S./M.S. years unverified).

`db._prose` reads education items with `created_by='crawler'`, so this row is
tagged 'crawler' to be surfaced. KNOWN FRAGILITY: reconcile is created_by-scoped;
a future `run_explore.py` crawl of js2852 (whose stub page yields no education)
would retire this item. Re-run this script after any js2852 recrawl, or make it
durable later by broadening `_prose` to accept a manual source. Idempotent on the
education natural_key.

Dry-run by default; `--commit` takes a hardened_backup first.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._area_tag_migrate import hardened_backup  # noqa: E402

KEY = "people.njit.edu/profile/js2852"
NAT_KEY = f"{KEY}:education:main"
ORG_ID = 5  # matches her existing profile KB item (id 159)
# Layout-A component form: degree; institution; field; YEAR  → "Ph.D. Computer Science,
# Washington University in St. Louis (2006)" via facultyfolio.format.format_education.
CONTENT = ("Education of Jamie Payton (Computer Science): "
           "Ph.D.; Washington University in St. Louis; Computer Science; 2006")
SOURCE_URL = "https://www.linkedin.com/in/jamie-payton/"
METADATA = json.dumps({
    "entity_id": KEY,
    "verified": True,
    "natural_key": NAT_KEY,
    "source": "linkedin_manual",
    "note": "PhD only; year 2006 (conferred). B.S./M.S. years unverified, omitted.",
})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        "SELECT id, is_active FROM knowledge_items WHERE type='education' "
        "AND metadata LIKE ?", (f'%"natural_key": "{NAT_KEY}"%',),
    ).fetchone()

    if existing:
        print(f"IDEMPOTENT: education item already exists (id={existing['id']}, "
              f"active={existing['is_active']}) — nothing to do.")
        conn.close()
        return

    print("Will INSERT knowledge_item:")
    print(f"  type=education  created_by=crawler  org_id={ORG_ID}  is_active=1")
    print(f"  title=Jamie Payton — Education")
    print(f"  content={CONTENT}")
    print(f"  source_url={SOURCE_URL}")
    print(f"  metadata={METADATA}")

    if not args.commit:
        print("\nDRY RUN — re-run with --commit to write.")
        conn.close()
        return

    hardened_backup(args.db, "payton_edu")
    print("hardened_backup taken.")
    conn.execute(
        "INSERT INTO knowledge_items "
        "(org_id, type, title, content, metadata, source_url, is_active, created_by) "
        "VALUES (?, 'education', 'Jamie Payton — Education', ?, ?, ?, 1, 'crawler')",
        (ORG_ID, CONTENT, METADATA, SOURCE_URL),
    )
    conn.commit()
    new_id = conn.execute(
        "SELECT id FROM knowledge_items WHERE metadata LIKE ?",
        (f'%"natural_key": "{NAT_KEY}"%',),
    ).fetchone()["id"]
    conn.close()
    print(f"COMMITTED education item id={new_id}.")


if __name__ == "__main__":
    main()

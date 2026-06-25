"""One-off gated migration: move each Person's flat ``attrs.website`` into
``attrs.profiles.website.url`` so every external link lives in ONE place
(consistent with scholar/linkedin/orcid/github). Idempotent; dry-run by default.

  python scripts/_website_profiles_migrate.py             # dry-run (counts only)
  python scripts/_website_profiles_migrate.py --commit    # write (hardened_backup first)

project.py already stores website under profiles going forward; this backfills the
existing rows. profile_fields keeps a fallback read of the old flat path, so an
un-migrated node still renders — this just makes the live data schema-consistent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection


def migrate(conn, commit: bool) -> dict:
    rows = conn.execute(
        "SELECT id, attrs FROM nodes WHERE type='Person' AND attrs IS NOT NULL").fetchall()
    moved = already = skipped = 0
    for nid, raw in rows:
        attrs = json.loads(raw) if raw else {}
        site = attrs.get("website")
        if not site:
            continue
        profiles = dict(attrs.get("profiles") or {})
        entry = dict(profiles.get("website") or {})
        if entry.get("url") == site:
            # already under profiles; just drop the stray flat key
            attrs.pop("website", None)
            already += 1
        else:
            # flat value wins on disagreement — same as project_entity's re-crawl semantics
            # (website carries only a url, no metrics to preserve, so the crawler value always wins)
            entry["url"] = site
            profiles["website"] = entry
            attrs["profiles"] = profiles
            attrs.pop("website", None)
            moved += 1
        if commit:
            conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (json.dumps(attrs), nid))
    return {"people_with_website": moved + already, "moved": moved,
            "already_under_profiles": already}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalize Person website into profiles.website.url")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        print("backup:", hardened_backup(args.db, label="website-profiles"))

    conn = get_connection(args.db)
    out = migrate(conn, args.commit)
    if args.commit:
        conn.commit()
        print("COMMITTED", out)
    else:
        print("DRY RUN", out, "(use --commit to write)")
    return out


if __name__ == "__main__":
    main()

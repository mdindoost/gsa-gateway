#!/usr/bin/env python
"""Ingest the GSA officer/RGO roster (bot/data/gsa_people.yml) into the graph.
Dry-run by default; --commit takes a hardened backup first. Idempotent + reconciling
(officers no longer in the file are retired)."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import yaml
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion.roster import project_roster, reconcile_roster


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--roster", default=str(REPO / "bot" / "data" / "gsa_people.yml"))
    ap.add_argument("--commit", action="store_true", help="write (hardened backup first)")
    args = ap.parse_args(argv)

    roster = yaml.safe_load(Path(args.roster).read_text(encoding="utf-8"))
    n_people = len(roster.get("people", [])) + sum(len(r.get("people", [])) for r in roster.get("rgos", []))
    print(f"roster: {n_people} people, {len(roster.get('rgos', []))} RGO(s)")
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-gsa-people")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        present = project_roster(conn, roster)
        retired = reconcile_roster(conn, present)
    print(f"committed: {len(present)} appointment(s) projected, {retired} retired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

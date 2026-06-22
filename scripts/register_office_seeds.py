#!/usr/bin/env python
"""Gated, idempotent registration of the Phase-1 Wave-1 office entry points (spec §6 / Plan D).
Creates the EOS org (existing orgs are get-or-create, names preserved) and registers each entry
point into crawl_entry_points. Dry-run default; --commit takes a hardened backup. Harvest next
with scripts/harvest_office.py."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion import entry_point_store as eps

WAVE1 = [
    dict(url="https://www.njit.edu/parking/", scope_prefix="/parking/", org_slug="eos",
         org_name="Environmental and Operational Services (EOS)"),
    dict(url="https://www.njit.edu/mailroom/", scope_prefix="/mailroom/", org_slug="eos"),
    dict(url="https://www.njit.edu/sustainability/", scope_prefix="/sustainability/", org_slug="eos"),
    dict(url="https://www.njit.edu/environmentalsafety/", scope_prefix="/environmentalsafety/", org_slug="eos"),
    dict(url="https://www.njit.edu/global/", scope_prefix="/global/", org_slug="ogi"),
    dict(url="https://www.njit.edu/bursar/", scope_prefix="/bursar/", org_slug="bursar"),
    dict(url="https://www.njit.edu/registrar/", scope_prefix="/registrar/", org_slug="registrar"),
]
INTERVAL_DAYS = 30          # owner-tunable recurrence cadence


def register(conn) -> dict:
    """Ensure each org exists (EOS created; existing orgs NOT renamed) and register each entry
    point. Idempotent (ensure_org is get-or-create; add_seed upserts to active)."""
    seen_orgs: set[str] = set()
    for ep in WAVE1:
        slug = ep["org_slug"]
        if slug not in seen_orgs:
            # ensure_org is get-or-create: only the EOS row is new; existing org names are kept.
            ensure_org(conn, slug=slug, name=ep.get("org_name", slug), parent_slug="njit", type="office")
            seen_orgs.add(slug)
        eps.add_seed(conn, url=ep["url"], scope_prefix=ep["scope_prefix"], org_slug=slug,
                     parent_slug="njit", org_type="office", crawl_interval_days=INTERVAL_DAYS)
    sync_org_nodes(conn)
    return {"orgs": len(seen_orgs), "entry_points": len(WAVE1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    print(f"register Wave-1: {len(WAVE1)} entry points → orgs eos/ogi/bursar/registrar")
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-register")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        print("  ", register(conn))
    print("next: python scripts/harvest_office.py --commit  (dev-copy first), then v2/scripts/embed_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

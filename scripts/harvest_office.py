"""Gated harvest of ONE office entry point's prose sub-tree into the KB as type='office_page'.
crawl_site (relevance_gated=False) → quality gate → hybrid ingest. Dry-run default;
--commit takes a hardened backup. Embed afterwards with v2/scripts/embed_all.py. spec Plan A."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import sync_org_nodes
from v2.core.ingestion import entry_point_store as eps
from v2.core.ingestion.office_ingest import ingest_office_page
from v2.core.ingestion.office_quality import dedup_boilerplate, is_low_quality
from v2.core.ingestion.web_crawler import crawl_site, fetch_with_status


def harvest_entry_point(conn, ep_row, fetch, *, budget: int = 60, depth: int = 3) -> dict:
    """Crawl one entry point's sub-tree, quality-gate, ingest. fetch(url)->(html|None,status)."""
    seed = ep_row["url"]
    res = crawl_site(seed, lambda u: fetch(u)[0], max_depth=depth, budget=budget,
                     relevance_gated=False)
    pages = dedup_boilerplate([(p.url, p.text) for p in res.pages])
    row = conn.execute("SELECT id FROM organizations WHERE slug=?", (ep_row["org_slug"],)).fetchone()
    if not row:
        raise ValueError(f"org slug {ep_row['org_slug']!r} not found — create the office org before harvesting it")
    org_id = row[0]
    stats = {"pages": len(pages), "chunked": 0, "staged": 0, "dropped": 0}
    for url, text in pages:
        if is_low_quality(text):
            stats["dropped"] += 1
            continue
        title = (text.splitlines()[0][:80] if text.strip() else url)
        n, leg = ingest_office_page(conn, org_id=org_id, url=url, title=title, text=text)
        stats["chunked" if leg == "chunk" else "staged"] += 1
    eps.mark_crawled(conn, ep_row["id"])
    sync_org_nodes(conn)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--entry-id", type=int, help="crawl_entry_points.id to harvest (default: all active)")
    ap.add_argument("--budget", type=int, default=60)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    rows = (conn.execute("SELECT * FROM crawl_entry_points WHERE id=?", (args.entry_id,)).fetchall()
            if args.entry_id else eps.list_active(conn, aspect="office"))
    print(f"office harvest: {len(rows)} active entry point(s)")
    if not rows:
        return 0
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-harvest")
    print(f"backup: {bkp.name}")
    fetch = fetch_with_status()
    with conn:
        for row in rows:
            stats = harvest_entry_point(conn, row, fetch, budget=args.budget, depth=args.depth)
            print(f"  {row['url']}: {stats}")
    print("next: python v2/scripts/embed_all.py  (then review staged high-stakes pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

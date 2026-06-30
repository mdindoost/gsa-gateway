"""Gated runner for the catalog.njit.edu prose crawler (Build A).

Dry-run by default; --commit writes the live DB (hardened_backup first). Sitemap-driven: the
frontier IS catalog.njit.edu/sitemap.xml. People are owned by explore.py; college subdomain prose
by college_crawl. This owns catalog.njit.edu (created_by='catalog_crawl').

Gated:  cp gsa_gateway.db /tmp/dev.db
        python scripts/crawl_catalog.py --db /tmp/dev.db            # dry-run
        python scripts/crawl_catalog.py --db /tmp/dev.db --commit   # dev write, inspect + verify_kg
        python scripts/crawl_catalog.py --commit --embed             # live (owner-gated)

Spec: docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import get_connection
from v2.core.ingestion.catalog_crawl import (
    CATALOG_SOURCE, catalog_seed_urls, iter_catalog_groups, reconcile_catalog)
from v2.core.ingestion.college_crawl import ingest_college, ingest_pdf_pages
from v2.core.ingestion.web_crawler import make_fetcher, make_bytes_fetcher


def main(argv=None):
    ap = argparse.ArgumentParser(description="catalog.njit.edu prose crawler (Build A)")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true", help="Write live DB (hardened_backup first)")
    ap.add_argument("--embed", action="store_true", help="Run embed_all.py + embed_chunks.py after commit")
    ap.add_argument("--delay", type=float, default=0.3, help="Politeness delay between fetches (s)")
    ap.add_argument("--limit", type=int, default=0, help="Dev: only first N sitemap URLs (forces --no-reconcile)")
    ap.add_argument("--no-reconcile", action="store_true", help="Skip the retirement pass")
    args = ap.parse_args(argv)

    if args.limit:
        args.no_reconcile = True   # S5: a partial frontier must never retire

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="catalog-crawl")

    conn = get_connection(args.db)
    fetch = make_fetcher()
    fetch_bytes = make_bytes_fetcher()

    # Sample the retirement-floor baseline BEFORE ingest (catalog_crawl/policy only).
    prior = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by=? AND type='policy'",
        (CATALOG_SOURCE,)).fetchone()[0]

    urls = catalog_seed_urls(fetch_bytes)
    if not urls:
        print("ERROR: catalog sitemap returned no URLs — aborting (no destructive action taken).")
        sys.exit(2)
    if args.limit:
        urls = urls[:args.limit]
    print(f"frontier: {len(urls)} catalog URLs (prior active catalog policy rows: {prior})")

    def _delayed_fetch(u):
        h = fetch(u)
        if args.delay:
            time.sleep(args.delay)
        return h

    totals = {"prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0,
              "pdf_inserted": 0, "pdf_updated": 0, "pdf_unchanged": 0, "skipped": 0}
    for slug, name, parent, otype, res in iter_catalog_groups(urls, _delayed_fetch):
        out = ingest_college(conn, slug, name, parent, res, res.html_by_url,
                             org_type=otype, created_by=CATALOG_SOURCE)
        pdf_items = [(u, t) for p in res.prose for u, t in p.files if u.lower().endswith(".pdf")]
        if pdf_items:
            pout = ingest_pdf_pages(conn, slug, name, parent, pdf_items, fetch_bytes,
                                    org_type=otype, created_by=CATALOG_SOURCE)
            for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                totals[k] += pout[k]
        for k in ("prose_inserted", "prose_updated", "prose_unchanged"):
            totals[k] += out[k]
        totals["skipped"] += out["skipped"]
        print(f"  {slug}: prose +{out['prose_inserted']} ~{out['prose_updated']} "
              f"={out['prose_unchanged']} skipped {out['skipped']}")

    if not args.no_reconcile:
        rec = reconcile_catalog(conn, urls, prior)
        print(f"retirement: {rec}")
    else:
        print("retirement: skipped (--no-reconcile/--limit)")

    print("totals:", totals)

    if args.commit:
        conn.commit()
        print("COMMITTED")
        if args.embed:
            # Pass the SAME --db through so --embed targets the DB we just wrote (else the embed
            # scripts default to the live gsa_gateway.db — wrong for a dev-copy run).
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py"), args.db],
                           check=True)
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_chunks.py"),
                            "--db", args.db], check=True)
    else:
        print("DRY RUN — no commit (use --commit to write)")
    return totals


if __name__ == "__main__":
    main()

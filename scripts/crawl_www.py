"""Gated runner for the www.njit.edu prose crawler (Build B).

Dry-run by default (writes NOTHING — the uncommitted transaction is discarded); --commit writes the
live DB (hardened_backup first). Sitemap-driven over every www.njit.edu subsite + the main sitemap
(created_by='njit_www_crawl'). People are owned by explore.py; college-subdomain prose by
college_crawl; catalog by catalog_crawl. This owns www.njit.edu prose.

The DRY-RUN IS THE INSPECTION (owner 2026-06-30: no dev-copy cycle — dry-run + hardened_backup):
it prints per-org counts, the TYPE distribution (recency-typing audit), dedup-drops, the stale-dup ⚠
list, and sample page texts (nav-chrome spot-check) — review, fix classify_type/registry if needed,
re-run, then --commit.

Gated:  python scripts/crawl_www.py                       # full read-only dry-run (inspect)
        python scripts/crawl_www.py --entry bursar        # one-subsite dry-run
        python scripts/crawl_www.py --commit --embed      # live (hardened_backup; embed both)

Spec: docs/superpowers/specs/2026-06-30-www-crawl-build-b-design.md
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
from v2.core.ingestion import www_crawl as W
from v2.core.ingestion.web_crawler import make_fetcher, make_bytes_fetcher


def _select_entries(entry: str | None):
    if not entry:
        return list(W.WWW_SUBSITES)
    from urllib.parse import urlsplit

    def _match(e):
        host = urlsplit(e.sitemap_url).netloc          # e.g. cs.njit.edu or www.njit.edu
        return (e.org_slug == entry                    # org slug (computer-science)
                or host == entry                        # full host (cs.njit.edu)
                or host.startswith(entry + ".")         # subdomain shorthand (cs -> cs.njit.edu)
                or f"/{entry}/" in e.sitemap_url)        # www path section (/bursar/)
    sel = [e for e in W.WWW_SUBSITES if _match(e)]
    if not sel:
        print(f"ERROR: --entry {entry!r} matched no subsite. Known slugs/sections:")
        print("  " + ", ".join(sorted({e.org_slug for e in W.WWW_SUBSITES})))
        sys.exit(2)
    return sel


def _report(conn, source):
    """Inspection report from the (possibly uncommitted) transaction — the dry-run audit."""
    print("\n--- inspection (njit_www_crawl) ---")
    by_type = conn.execute(
        "SELECT type, COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by=? "
        "GROUP BY type ORDER BY 2 DESC", (source,)).fetchall()
    print("type distribution:", {t: n for t, n in by_type})
    by_org = conn.execute(
        "SELECT o.slug, COUNT(*) FROM knowledge_items k JOIN organizations o ON o.id=k.org_id "
        "WHERE k.is_active=1 AND k.created_by=? GROUP BY o.slug ORDER BY 2 DESC", (source,)).fetchall()
    print("per-org counts:", {s: n for s, n in by_org})
    print("sample pages (title — first ~160 chars):")
    for title, content, url in conn.execute(
            "SELECT title, substr(content,1,160), source_url FROM knowledge_items "
            "WHERE is_active=1 AND created_by=? ORDER BY id DESC LIMIT 5", (source,)).fetchall():
        print(f"  [{title}] {url}\n    {content!r}")
    # Roster-leak audit (focused-review MAJOR backstop): kept pages whose URL still smells like a
    # people-list despite _roster_skip + is_people_path — eyeball these before keeping the live write.
    leaks = conn.execute(
        "SELECT source_url, title FROM knowledge_items WHERE is_active=1 AND created_by=? AND ("
        "lower(source_url) LIKE '%faculty%' OR lower(source_url) LIKE '%staff%' OR "
        "lower(source_url) LIKE '%/people%' OR lower(source_url) LIKE '%directory%' OR "
        "lower(source_url) LIKE '%personnel%' OR lower(source_url) LIKE '%leadership%')", (source,)).fetchall()
    print(f"roster-leak audit: {len(leaks)} kept page(s) with people-tokens in URL (eyeball):")
    for url, title in leaks[:30]:
        print(f"  ? [{title}] {url}")
    if len(leaks) > 30:
        print(f"  … and {len(leaks) - 30} more")


def main(argv=None):
    ap = argparse.ArgumentParser(description="www.njit.edu prose crawler (Build B)")
    ap.add_argument("--db", default="gsa_gateway.db")
    ap.add_argument("--commit", action="store_true", help="Write live DB (hardened_backup first)")
    ap.add_argument("--embed", action="store_true", help="Run embed_all.py + embed_chunks.py after commit")
    ap.add_argument("--delay", type=float, default=0.3, help="Politeness delay between fetches (s)")
    ap.add_argument("--entry", default=None, help="Crawl ONE subsite (slug/section); forces --no-reconcile")
    ap.add_argument("--no-reconcile", action="store_true", help="Skip the retirement pass")
    ap.add_argument("--limit", type=int, default=0, help="Dev: first N urls per entry (forces --no-reconcile)")
    args = ap.parse_args(argv)

    # A partial frontier (single subsite or --limit) must NEVER retire — otherwise reconcile would
    # mass-retire every OTHER subsite's rows whose URLs aren't in this run's small union (S5).
    reconcile = not (args.no_reconcile or args.entry or args.limit)

    if args.commit:
        from scripts._area_tag_migrate import hardened_backup
        hardened_backup(args.db, label="www-crawl")

    conn = get_connection(args.db)
    fetch = make_fetcher()
    fetch_bytes = make_bytes_fetcher()

    def _delayed_fetch(u):
        h = fetch(u)
        if args.delay:
            time.sleep(args.delay)
        return h

    entries = _select_entries(args.entry)
    print(f"crawling {len(entries)} subsite(s); reconcile={reconcile}")
    out = W.run(conn, _delayed_fetch, fetch_bytes, entries=entries,
                reconcile=reconcile, limit=args.limit)

    print("totals:", out["totals"])
    print(f"sitemap union: {out['union']} URLs")
    print("retirement:", out["reconcile"])
    if out["warnings"]:
        print(f"\n⚠ {len(out['warnings'])} possible stale duplicate(s) — review for gated retire:")
        for w in out["warnings"][:40]:
            print("  -", w)
        if len(out["warnings"]) > 40:
            print(f"  … and {len(out['warnings']) - 40} more")

    _report(conn, W.SOURCE)

    if args.commit:
        conn.commit()
        print("\nCOMMITTED")
        if args.embed:
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py"), args.db], check=True)
            subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_chunks.py"), "--db", args.db],
                           check=True)
    else:
        print("\nDRY RUN — no commit (use --commit to write)")
    return out


if __name__ == "__main__":
    main()

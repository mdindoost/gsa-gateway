#!/usr/bin/env python3
"""Crawl www.njit.edu/counseling/ (whole subtree, path-scoped DFS) -> KB prose + KG staff.

Org 19 is the Counseling Center (C-CAPS). The 7-person roster lives on /counseling/c-caps-staff
(email-anchored, credential suffix). People are URL-gated to that page; every other page is prose-only.
Counseling = sensitive content (mental health) -> served verbatim, heads-up covers it. Dry-run by
default; --commit takes a hardened backup first, then writes via ccaps_crawl.ingest_ccaps and commits.
DB-only change -> no bot restart; run embed_all afterwards (or pass --embed).

Gated:  cp gsa_gateway.db /tmp/dev.db
        python scripts/crawl_ccaps.py --db /tmp/dev.db [--commit]   # dev
        python scripts/crawl_ccaps.py --commit --embed              # live
Then: python scripts/_ccaps_cleanup_migrate.py [--commit] ; python scripts/_ccaps_alias_migrate.py [--commit]
Spec: docs/superpowers/specs/2026-06-24-ccaps-crawl-design.md
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

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion import ccaps_crawl
from v2.core.ingestion.web_crawler import make_fetcher

ENTRY_POINTS = ["https://www.njit.edu/counseling/"]


def _polite_fetcher(delay: float):
    base = make_fetcher()
    def fetch(url: str):
        time.sleep(delay)
        return base(url)
    return fetch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write to the DB (else dry run)")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--delay", type=float, default=0.3, help="politeness delay between fetches")
    ap.add_argument("--embed", action="store_true", help="run embed_all after a --commit")
    ap.add_argument("--only", help="substring filter to crawl a subset of entry points")
    args = ap.parse_args(argv)

    fetch = _polite_fetcher(args.delay)
    seeds = [s for s in ENTRY_POINTS if not args.only or args.only in s]
    if not seeds:
        sys.exit(f"no entry point matches --only={args.only!r}")

    results = []
    for seed in seeds:
        print(f"\n# {seed}")
        res = ccaps_crawl.extract_entry(seed, fetch, budget=400)
        results.append(res)
        trunc = "  ⚠ TRUNCATED (hit budget — raise it)" if res.truncated else ""
        print(f"  staff={len(res.staff)}  prose={len(res.prose)}  skipped={len(res.skipped)}{trunc}")
        for s in res.staff:
            print(f"    * {s.name} — {s.title} — {s.email} — [{s.unit}]")
        for p in sorted(res.prose, key=lambda x: x.source_url):
            fig = f"  [img:{len(p.images)} file:{len(p.files)}]" if (p.images or p.files) else ""
            print(f"    - {p.title[:44]:44} | {len(p.content):6}ch | "
                  f"{p.source_url.replace('https://www.njit.edu', '')}{fig}")
        for u in res.skipped:
            print(f"    ! SKIP (no content): {u}")
        for w in res.warnings:
            print(f"    ⚠ ROSTER WARNING: {w}")

    tot_staff = sum(len(r.staff) for r in results)
    tot_prose = sum(len(r.prose) for r in results)
    tot_skip = sum(len(r.skipped) for r in results)
    tot_warn = sum(len(r.warnings) for r in results)
    print(f"\n=== TOTAL  staff={tot_staff}  prose={tot_prose}  skipped={tot_skip}  warnings={tot_warn} ===")

    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    hardened_backup(args.db, "pre-ccaps-crawl")
    conn = get_connection(args.db)
    summary = {"staff": 0, "prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0}
    for res in results:
        r = ccaps_crawl.ingest_ccaps(conn, res)
        for k in summary:
            summary[k] += r.get(k, 0)
    conn.commit()
    print(f"WROTE: {summary}")

    if args.embed:
        print("embedding…")
        subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py"), args.db], check=True)
    else:
        print("next: python v2/scripts/embed_all.py   # embed the new C-CAPS chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

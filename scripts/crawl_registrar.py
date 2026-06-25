#!/usr/bin/env python3
"""Crawl www.njit.edu/registrar/ (whole subtree, path-scoped DFS) -> KB prose + KG staff.

The Registrar publishes a staff directory (Name / Phone / Functions table), so the crawl
normally yields 13 staff + the service-page prose. Dry-run by default (crawls + prints the
manifest, no DB writes). --commit takes a hardened backup first, then writes via
registrar_crawl.ingest_registrar and commits. DB-only change -> no bot restart; run embed_all
afterwards (or pass --embed).

Gated workflow:  cp gsa_gateway.db /tmp/dev.db
                 python scripts/crawl_registrar.py --db /tmp/dev.db            # dev dry-run
                 python scripts/crawl_registrar.py --db /tmp/dev.db --commit   # dev write, inspect
                 python scripts/crawl_registrar.py --commit --embed            # live

After the live crawl verifies, run the SEPARATE clean-replace migration to retire the old
pre-crawler rows: python scripts/_registrar_cleanup_migrate.py (dry-run; --commit).

Spec: docs/superpowers/specs/2026-06-24-registrar-crawl-design.md
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
from v2.core.ingestion import registrar_crawl
from v2.core.ingestion.web_crawler import make_fetcher

# Registrar is one office at one path-prefix; a single homepage seed walks the whole subtree.
ENTRY_POINTS = ["https://www.njit.edu/registrar/"]


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
        res = registrar_crawl.extract_entry(seed, fetch, budget=400)   # whole subtree backstop
        results.append(res)
        trunc = "  ⚠ TRUNCATED (hit budget — raise it)" if res.truncated else ""
        print(f"  staff={len(res.staff)}  prose={len(res.prose)}  skipped={len(res.skipped)}{trunc}")
        for s in res.staff:
            print(f"    * {s.name} — {s.title} — {s.phone} — {s.email} — [{s.unit}]")
        for p in sorted(res.prose, key=lambda x: x.source_url):
            fig = f"  [img:{len(p.images)} file:{len(p.files)}]" if (p.images or p.files) else ""
            print(f"    - {p.title[:44]:44} | {len(p.content):6}ch | "
                  f"{p.source_url.replace('https://www.njit.edu', '')}{fig}")
            for u, txt in p.files:                       # ND5: enumerate flagged PDF/asset links
                print(f"        · file (page+link only, content not extracted): {u}")
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

    hardened_backup(args.db, "pre-registrar-crawl")
    conn = get_connection(args.db)
    summary = {"staff": 0, "prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0}
    for res in results:
        r = registrar_crawl.ingest_registrar(conn, res)
        for k in summary:
            summary[k] += r.get(k, 0)
    conn.commit()
    print(f"WROTE: {summary}")

    if args.embed:
        print("embedding…")
        subprocess.run([sys.executable, str(REPO / "v2/scripts/embed_all.py"), args.db], check=True)
    else:
        print("next: python v2/scripts/embed_all.py   # embed the new Registrar chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

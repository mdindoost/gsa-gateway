#!/usr/bin/env python
"""Stage NJIT sitemap pages for grounded extraction (discovery + fetch + clean).

Reads the NJIT sitemaps, filters URLs to a chosen bucket (substring match), fetches each page
politely (project UA, delay), strips boilerplate with web_crawler.clean_text, and writes the
cleaned text to /tmp/njit_crawl/<slug>.txt (with a SOURCE_URL header) + a manifest. A Haiku
subagent then extracts verbatim facts from these staged files; scripts/_crawl_ground_filter.py
verifies them; scripts/_crawl_ingest.py ingests the survivors as source='crawler'.

Usage: python scripts/_crawl_stage.py --bucket /about-university/ --prefix about
       python scripts/_crawl_stage.py --bucket /graduate/ --prefix grad --limit 40
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.explore import http_fetch
from v2.core.ingestion.web_crawler import clean_text

SITEMAPS = ["https://www.njit.edu/sitemap.xml", "https://catalog.njit.edu/sitemap.xml"]
STAGE = Path("/tmp/njit_crawl")


def sitemap_urls(url: str) -> list[str]:
    _, body, status = http_fetch(url)
    return re.findall(r"<loc>(.*?)</loc>", body) if status == "ok" else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True, help="URL substring to keep, e.g. /graduate/")
    ap.add_argument("--prefix", required=True, help="slug prefix for staged files")
    ap.add_argument("--limit", type=int, default=0, help="cap pages (0 = all)")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args(argv)

    STAGE.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []
    for sm in SITEMAPS:
        urls += [u for u in sitemap_urls(sm) if args.bucket in u]
    urls = sorted(set(urls))
    if args.limit:
        urls = urls[: args.limit]
    print(f"bucket {args.bucket!r}: staging {len(urls)} pages")

    manifest = []
    for u in urls:
        fu, html, st = http_fetch(u)
        if st != "ok" or not html:
            print("  SKIP", st, u)
            continue
        txt = clean_text(html)
        tail = u.split(args.bucket)[-1].strip("/") or "index"
        slug = f"{args.prefix}__" + re.sub(r"[^a-z0-9]+", "-", tail.lower())[:70]
        (STAGE / f"{slug}.txt").write_text(f"SOURCE_URL: {u}\n\n{txt}", encoding="utf-8")
        manifest.append({"url": u, "slug": slug, "chars": len(txt)})
        time.sleep(args.delay)
    json.dump(manifest, open(STAGE / f"manifest_{args.prefix}.json", "w"), indent=2)
    print(f"staged {len(manifest)} pages -> {STAGE}/  (manifest_{args.prefix}.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

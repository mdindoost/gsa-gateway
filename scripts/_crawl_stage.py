#!/usr/bin/env python
"""Stage NJIT sitemap pages for grounded extraction (discovery + fetch + clean).

Reads the NJIT sitemaps, filters URLs to a chosen bucket (substring match), fetches each page
politely (project UA, delay), strips boilerplate with web_crawler.clean_text, and writes the
cleaned text to /tmp/njit_crawl/<slug>.txt (with a SOURCE_URL header) + a manifest. A Haiku
subagent then extracts verbatim facts from these staged files; scripts/_crawl_ground_filter.py
verifies them; scripts/_crawl_ingest.py ingests the survivors as source='crawler'.

Two discovery modes:
  --bucket: sitemap mode (existing) — keep sitemap URLs containing a substring.
  --seed/--follow: seed mode (for sites NOT in the sitemap, e.g. the /parking/ EOS multisite) —
      fetch hub URL(s) with a robots-aware fetcher and follow same-host links under given path
      prefixes (depth 1). select_seed_links is the pure helper; near-empty (JS-only) pages are
      skipped + flagged.

Usage: python scripts/_crawl_stage.py --bucket /about-university/ --prefix about
       python scripts/_crawl_stage.py --bucket /graduate/ --prefix grad --limit 40
       python scripts/_crawl_stage.py --seed https://www.njit.edu/parking/ \\
           --follow '/parking/,/mailroom,/sustainability,/environmentalsafety,/about/transportation' \\
           --prefix eos
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

from v2.core.ingestion.explore import http_fetch
from v2.core.ingestion.web_crawler import (
    clean_text, is_non_html, make_fetcher, normalize_url, same_site)

SITEMAPS = ["https://www.njit.edu/sitemap.xml", "https://catalog.njit.edu/sitemap.xml"]
STAGE = Path("/tmp/njit_crawl")

# Front-end assets is_non_html() doesn't cover (it targets documents, not page chrome).
_ASSET_EXT = (".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map")


def sitemap_urls(url: str) -> list[str]:
    _, body, status = http_fetch(url)
    return re.findall(r"<loc>(.*?)</loc>", body) if status == "ok" else []


def select_seed_links(base_url: str, html: str, follow_prefixes: list[str]) -> list[str]:
    """From one fetched page, return the same-host links to follow (depth 1): normalise each
    href against ``base_url`` (drops fragment + query, lowercases host), keep only same-host
    URLs whose PATH starts with one of ``follow_prefixes`` (prefix-anchored, not substring),
    drop document + chrome assets, dedupe. Pure: no I/O. Renamed vs web_crawler.select_links
    (relevance-gated) to avoid collision."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        return []
    kept: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        url = normalize_url(href, base_url)
        if not url.startswith("http") or not same_site(base_url, url):
            continue
        path = urlparse(url).path or "/"
        if not any(path.startswith(p) for p in follow_prefixes):
            continue
        if is_non_html(url) or path.lower().endswith(_ASSET_EXT):
            continue
        kept.add(url)
    return sorted(kept)


MIN_BODY_CHARS = 200  # below this, a page is a JS-only shell / empty — skip and flag
SEED_CAP = 80         # runaway backstop for the seed/link-follow mode when --limit is 0


def _slug(prefix: str, url: str) -> str:
    """Stable slug from a URL path (e.g. /parking/visitor-parking -> eos__parking-visitor-parking)."""
    tail = (urlparse(url).path or "/").strip("/") or "index"
    return f"{prefix}__" + (re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")[:70] or "index")


def _http(url: str) -> str | None:
    """Trusted sitemap-mode fetch via the project UA (sitemap URLs are NJIT's own)."""
    _, html, st = http_fetch(url)
    return html if st == "ok" and html else None


def stage(urls, fetch, slug_of, *, delay: float = 0.0,
          min_chars: int = MIN_BODY_CHARS, stage_dir: Path = STAGE) -> list[dict]:
    """Fetch + clean each url, skip dead/near-empty pages (flagged), write
    ``<slug>.txt`` with a SOURCE_URL header. Returns the manifest. ``fetch(url)->html|None``
    is injected (real fetchers do UA/robots; tests pass a dict-backed stub)."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for u in urls:
        html = fetch(u)
        if not html:
            print("  SKIP fetch", u)
            continue
        txt = clean_text(html)
        if len(txt) < min_chars:
            print(f"  SKIP empty ({len(txt)} chars — JS-only/shell?)", u)
            continue
        slug = slug_of(u)
        (stage_dir / f"{slug}.txt").write_text(f"SOURCE_URL: {u}\n\n{txt}", encoding="utf-8")
        manifest.append({"url": u, "slug": slug, "chars": len(txt)})
        if delay:
            time.sleep(delay)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", help="sitemap mode: URL substring to keep, e.g. /graduate/")
    ap.add_argument("--seed", help="seed mode: hub URL(s) to fetch + follow (comma-separated)")
    ap.add_argument("--follow", default="",
                    help="seed mode: path prefixes to follow, comma-separated, e.g. /parking/,/mailroom")
    ap.add_argument("--prefix", required=True, help="slug prefix for staged files")
    ap.add_argument("--limit", type=int, default=0, help="cap pages (0 = all; seed mode caps at 80)")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args(argv)

    if bool(args.bucket) == bool(args.seed):
        ap.error("pass exactly one of --bucket or --seed")

    if args.bucket:
        urls: list[str] = []
        for sm in SITEMAPS:
            urls += [u for u in sitemap_urls(sm) if args.bucket in u]
        urls = sorted(set(urls))
        if args.limit:
            urls = urls[: args.limit]
        bucket = args.bucket
        slug_of = lambda u: f"{args.prefix}__" + (  # noqa: E731 - tiny mode-local slug
            re.sub(r"[^a-z0-9]+", "-", (u.split(bucket)[-1].strip("/") or "index").lower())[:70])
        fetch = _http
        print(f"bucket {args.bucket!r}: staging {len(urls)} pages")
    else:
        seeds = [s.strip() for s in args.seed.split(",") if s.strip()]
        follow = [p.strip() for p in args.follow.split(",") if p.strip()]
        if not follow:
            ap.error("--seed requires --follow (path prefixes to keep)")
        fetch = make_fetcher()
        urls = list(seeds)                                   # stage the hub(s) themselves too
        for s in seeds:
            html = fetch(s)
            if html:
                urls += select_seed_links(s, html, follow)
        urls = sorted(set(urls))[: (args.limit or SEED_CAP)]
        slug_of = lambda u: _slug(args.prefix, u)            # noqa: E731
        print(f"seed {seeds}: following {follow} -> staging {len(urls)} pages")

    manifest = stage(urls, fetch, slug_of, delay=args.delay)
    json.dump(manifest, open(STAGE / f"manifest_{args.prefix}.json", "w"), indent=2)
    print(f"staged {len(manifest)} pages -> {STAGE}/  (manifest_{args.prefix}.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

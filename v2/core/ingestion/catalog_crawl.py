"""Sitemap-driven crawler for catalog.njit.edu (Build A).

Brings the whole current NJIT catalog into knowledge_items as `catalog_crawl` prose. Reuses
college_crawl/eos_crawl extraction + ingest; the ONLY behavioral seam is the created_by param.
Makes NO serving/gating decisions (data-bringing-only hard line).

Spec: docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from v2.core.ingestion.college_crawl import EntryResult, is_people_path
from v2.core.ingestion.eos_crawl import (
    extract_prose, _url_rank, _strip_recurring_assets, _canon, _main_region,
)
from v2.core.ingestion.web_crawler import normalize_url

logger = logging.getLogger(__name__)

CATALOG_SOURCE = "catalog_crawl"
DEFAULT_SITEMAP = "https://catalog.njit.edu/sitemap.xml"
_SITEMAP_LOC = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
_DISALLOW_PREFIXES = ("/archive/",)  # robots-disallowed past-year trees (belt + suspenders)

_NJIT = ("njit", "New Jersey Institute of Technology", None, "university")
# catalog 2nd-level segment -> (org_slug, org_name, parent_slug, org_type). Names match existing orgs.
CATALOG_ORG_MAP: dict[str, tuple[str, str, str, str]] = {
    "computing-sciences":          ("ywcc", "YWCC", "njit", "college"),
    "science-liberal-arts":        ("csla", "College of Science and Liberal Arts", "njit", "college"),
    "newark-college-engineering":  ("nce", "Newark College of Engineering", "njit", "college"),
    "architecture-design":         ("hcad", "Hillier College of Architecture & Design", "njit", "college"),
    "management":                  ("mtsm", "Martin Tuchman School of Management (MTSM)", "njit", "college"),
    "honors-college":              ("honors", "Albert Dorman Honors College", "njit", "college"),
}


def org_for(url: str) -> tuple[str, str, str | None, str]:
    """Map a catalog URL to (org_slug, org_name, parent_slug, org_type) by its 2nd-level path
    segment (after graduate/undergraduate); anything else → njit root."""
    segs = [s for s in urlsplit(url).path.split("/") if s]
    if len(segs) >= 2 and segs[0] in ("graduate", "undergraduate") and segs[1] in CATALOG_ORG_MAP:
        return CATALOG_ORG_MAP[segs[1]]
    return _NJIT


def _norm(url: str) -> str:
    """Normalize ONCE: scheme→https + lowercased host (via normalize_url/_canon), then strip the
    trailing slash uniformly. This string is stored as source_url AND compared in retirement —
    nothing re-normalizes downstream (the S6 invariant)."""
    u = _canon(normalize_url(url, url))
    p = urlsplit(u)
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def catalog_seed_urls(fetch_bytes, sitemap_url: str = DEFAULT_SITEMAP) -> list[str]:
    """The current canonical catalog frontier from sitemap.xml. Fetched with fetch_bytes
    (make_bytes_fetcher) because make_fetcher rejects application/xml (B1). Drops empties +
    /archive/ (past years); normalizes + dedupes; preserves order."""
    data = fetch_bytes(sitemap_url)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for loc in root.iter(_SITEMAP_LOC):
        raw = (loc.text or "").strip()
        if not raw:
            continue
        if any(urlsplit(raw).path.startswith(pre) for pre in _DISALLOW_PREFIXES):
            continue
        u = _norm(raw)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _catalog_title(html: str) -> str | None:
    """Program-specific title for a CourseLeaf catalog page: the <h1> INSIDE the main content
    region (the site banner h1 'University Catalog 2025-2026' sits outside it), else the <title>
    tag's program part (CourseLeaf format 'Program < New Jersey Institute of Technology').
    Mechanical SELECTION of an existing heading — never rewrites. Returns None if neither exists
    (caller keeps extract_prose's title)."""
    soup = BeautifulSoup(html, "html.parser")
    region = _main_region(soup)
    h1 = region.find("h1") if region is not None else None
    if h1 and h1.get_text(" ", strip=True):
        return h1.get_text(" ", strip=True).rstrip(":").strip()
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True).split(" < ")[0].split(" | ")[0].strip()
    return None


def extract_urls(urls, fetch) -> EntryResult:
    """Extract prose from an EXPLICIT url list (no DFS). Skips people pages (explore.py owns
    people), dedups by content hash keeping the cleanest alias, stashes raw HTML for date
    extraction. Brings data only; no DB writes."""
    res = EntryResult(seed="catalog", prose=[], skipped=[])
    by_hash: dict[str, object] = {}
    order: list[str] = []
    for url in urls:
        if is_people_path(url):
            continue
        html = fetch(url)
        if not html:
            res.skipped.append(url)
            continue
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        title = _catalog_title(html)
        if title:
            page = replace(page, title=title)
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
            res.html_by_url[url] = html
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            res.html_by_url.pop(by_hash[h].source_url, None)
            by_hash[h] = page
            res.html_by_url[url] = html
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    return res


def iter_catalog_groups(urls, fetch):
    """Group urls by org_for, then extract each group. Yields one tuple per org group so the
    runner can ingest + release each group's HTML before the next (bounded peak memory, N1)."""
    groups: dict[tuple, list[str]] = {}
    for u in urls:
        groups.setdefault(org_for(u), []).append(u)
    for (slug, name, parent, otype), group_urls in groups.items():
        res = extract_urls(group_urls, fetch)
        yield slug, name, parent, otype, res


def reconcile_sitemap_set(conn, sitemap_urls, prior_active_count, *, created_by,
                          seen_hashes=frozenset(), types=("policy",),
                          min_floor=300, ratio=0.8) -> dict:
    """Retire active rows of one source (`created_by`) whose source_url left the sitemap `union`.
    Generalized from reconcile_catalog so Build A (catalog) and Build B (www) share it.

    - `types`: the row types subject to retirement (catalog: ('policy',); www adds news/event).
      'pdf' is never included → PDF asset rows are never retired (B2 — their natural_key is an
      asset URL, never a <loc>).
    - `seen_hashes` (SE-2): a row whose metadata.content_hash is in this set is NEVER retired even
      when its source_url ∉ union — its content was seen elsewhere THIS run (an aliased/renamed
      URL), so retiring it would lose content the dedup filter suppressed re-inserting.
    - Guards (S1): empty union → skip; len(union) < max(min_floor, ratio×prior) → skip (a partial/
      failed sitemap fetch never mass-retires). `prior_active_count` is the same-source/types active
      count sampled BEFORE this run's ingest (caller passes it).
    """
    sitemap = set(sitemap_urls)
    if not sitemap:
        return {"retired": 0, "skipped_reason": "empty_sitemap"}
    floor = max(min_floor, int(ratio * prior_active_count))
    if len(sitemap) < floor:
        logger.warning("reconcile_sitemap_set(%s): frontier %d < floor %d — skipping retirement",
                       created_by, len(sitemap), floor)
        return {"retired": 0, "skipped_reason": f"below_floor({len(sitemap)}<{floor})"}
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        "SELECT id, source_url, json_extract(metadata,'$.content_hash') FROM knowledge_items "
        f"WHERE is_active=1 AND created_by=? AND type IN ({placeholders})",
        (created_by, *types)).fetchall()
    retired = 0
    for rid, src, ch in rows:
        if src in sitemap or ch in seen_hashes:   # SE-2: content-survives-this-run guard
            continue
        conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                     "WHERE id=?", (rid,))
        retired += 1
    return {"retired": retired, "skipped_reason": None}


def reconcile_catalog(conn, sitemap_urls, prior_active_count, *, min_floor=300, ratio=0.8) -> dict:
    """Build A wrapper — retire active catalog_crawl POLICY rows whose source_url left the sitemap.
    Delegates to reconcile_sitemap_set (created_by=CATALOG_SOURCE, types=('policy',)) → byte-for-byte
    the same behavior as before the generalization."""
    return reconcile_sitemap_set(conn, sitemap_urls, prior_active_count,
                                 created_by=CATALOG_SOURCE, types=("policy",),
                                 min_floor=min_floor, ratio=ratio)

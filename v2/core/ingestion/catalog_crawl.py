"""Sitemap-driven crawler for catalog.njit.edu (Build A).

Brings the whole current NJIT catalog into knowledge_items as `catalog_crawl` prose. Reuses
college_crawl/eos_crawl extraction + ingest; the ONLY behavioral seam is the created_by param.
Makes NO serving/gating decisions (data-bringing-only hard line).

Spec: docs/superpowers/specs/2026-06-29-catalog-crawl-build-a-design.md
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit

from v2.core.ingestion.college_crawl import EntryResult, is_people_path
from v2.core.ingestion.eos_crawl import (
    extract_prose, _url_rank, _strip_recurring_assets, _canon,
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

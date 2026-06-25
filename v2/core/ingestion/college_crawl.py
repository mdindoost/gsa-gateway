"""Generalizable college/department PROSE crawler (Crawling 2.1).

Pilot: YWCC. Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB. Reuses the eos_crawl / web_crawler DFS spine AS-IS (already host+path scoped via
same_scope). Adds: URL-path page typing, structured date capture, in-host people-page skip,
distinct created_by for reconcile isolation. Makes NO serving/gating/usage decisions.

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion import entry_points as _ep
from v2.core.ingestion.eos_crawl import (
    ProsePage, extract_prose, _url_rank, _strip_recurring_assets, _canon, _in_scope,
)
from v2.core.ingestion.web_crawler import clean_text, normalize_url, select_links

logger = logging.getLogger(__name__)

# People-page segments = the LAST path segment of each SUPPLEMENTARY_PATH (the in-host people
# listings explore.py owns). Single source of truth — can't drift from the people crawler.
_PEOPLE_SEGMENTS = frozenset(p.strip("/").split("/")[-1].lower() for p in _ep.SUPPLEMENTARY_PATHS)

# URL-path segments that mark a page kind (segment match, not substring).
_NEWS_SEGMENTS = ("news", "announcement", "announcements")
_EVENT_SEGMENTS = ("event", "events")


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.lower().split("/") if s]


def extract_dates(html: str) -> dict:
    """Extract literal dates from STRUCTURED markup only (article:published_time, JSON-LD Event
    start/end, <time datetime>, dateModified). NO free-text parsing. Returns only present keys."""
    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    m = soup.find("meta", attrs={"property": "article:published_time"})
    if m and m.get("content"):
        out["published_at"] = m["content"].strip()

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if str(node.get("@type", "")).lower() == "event":
                if node.get("startDate"):
                    out.setdefault("event_start", str(node["startDate"]).strip())
                if node.get("endDate"):
                    out.setdefault("event_end", str(node["endDate"]).strip())
            if node.get("datePublished"):
                out.setdefault("published_at", str(node["datePublished"]).strip())
            if node.get("dateModified"):
                out.setdefault("source_updated_at", str(node["dateModified"]).strip())

    if "published_at" not in out:
        t = soup.find("time", attrs={"datetime": True})
        if t and t.get("datetime"):
            out["published_at"] = t["datetime"].strip()
    return out


def is_people_path(url: str) -> bool:
    """True when the URL is a dedicated people/roster page (skip — explore.py owns people).
    Segment match against entry_points.SUPPLEMENTARY_PATHS, so /faculty and /faculty/x match
    but /faculty-handbook (real prose) does not."""
    return any(s in _PEOPLE_SEGMENTS for s in _segments(url))


def classify_type(url: str) -> str:
    """Mechanically type a page by URL path SEGMENT (not substring): /news,/announcement(s) →
    news; /event(s) → event; else policy. A 'newsletter-signup' page is policy (segment match)."""
    segs = _segments(url)
    if any(s in _NEWS_SEGMENTS for s in segs):
        return "news"
    if any(s in _EVENT_SEGMENTS for s in segs):
        return "event"
    return "policy"


@dataclass
class EntryResult:
    seed: str
    prose: list[ProsePage]
    skipped: list[str]   # pages with no readable content (flag, never stored)
    truncated: bool = False   # hit the page budget with links still queued
    html_by_url: dict = field(default_factory=dict)  # raw HTML per kept page (for date extraction)


def crawl_entry(seed, fetch, max_depth=4, budget=400, delay=0.0, stats=None):
    """DFS the seed's subdomain (bare-host seed → whole host). Reuses select_links/same_scope
    (already host-scoped) + the eos seed-path guard. Yields (url, html). Politeness delay added."""
    seed = _canon(normalize_url(seed, seed))
    seed_path = urlparse(seed).path or "/"
    seen = {seed}
    stack = [(seed, 0)]
    while stack and len(seen) <= budget:
        url, depth = stack.pop()
        html = fetch(url)
        if delay:
            time.sleep(delay)
        if not html:
            continue
        yield url, html
        if depth < max_depth:
            follow, _ = select_links(html, url, seed, relevance_gated=False)
            for u in sorted((_canon(u) for u in follow), reverse=True):
                if u not in seen and _in_scope(seed_path, urlparse(u).path):
                    seen.add(u)
                    stack.append((u, depth + 1))
    if stats is not None:
        stats["truncated"] = bool(stack)
        if stack:
            logger.warning("crawl_entry: hit budget %d at %s; %d links unfollowed",
                           budget, seed, len(stack))


def extract_entry(seed, fetch, max_depth=4, budget=400, delay=0.0) -> EntryResult:
    """Crawl one prose entry point. Skip people pages (explore.py owns people). Dedup prose by
    content hash (collapse .php/clean-URL aliases). Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), prose=[], skipped=[])
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget,
                                 delay=delay, stats=stats):
        if is_people_path(url):
            continue                                  # people page — explore.py owns it
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
            res.html_by_url[url] = html              # stash raw HTML for date extraction
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            old_url = by_hash[h].source_url
            res.html_by_url.pop(old_url, None)
            by_hash[h] = page
            res.html_by_url[url] = html
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    res.truncated = stats.get("truncated", False)
    return res


PROSE_SOURCE = "college_crawl"


def ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url) -> dict:
    """Write an EntryResult's prose into knowledge_items under one org:
      type = classify_type(url); dates from extract_dates(raw html); created_by=PROSE_SOURCE.
    Content-hash idempotent (unchanged skipped; changed version-bumps old). NO Person creation.
    Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, org_slug, org_name, parent_slug=parent_slug, type="college")
    sync_org_nodes(conn)
    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": PROSE_SOURCE,
        }
        meta.update(extract_dates(html_by_url.get(p.source_url, "")))
        ptype = classify_type(p.source_url)
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, PROSE_SOURCE)).fetchone()
        if row and row[1] == ch:
            unchanged += 1
            continue
        if row:
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE id=?", (row[0],))
            updated += 1
        else:
            inserted += 1
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, ptype, p.title, p.content, json.dumps(meta), p.source_url, PROSE_SOURCE))
    return {"org_id": org_id, "prose_inserted": inserted, "prose_updated": updated,
            "prose_unchanged": unchanged, "skipped": len(result.skipped)}


@dataclass(frozen=True)
class ProseEntry:
    seed: str           # bare-host root, e.g. https://cs.njit.edu/
    org_slug: str
    org_name: str
    parent_slug: str


# YWCC pilot. Add a college/dept = add a ProseEntry here (the data registry — no new code).
# Data Science host confirmed at dry-run (Task E2); update the seed if it differs.
PROSE_ENTRY_POINTS: list[ProseEntry] = [
    ProseEntry("https://computing.njit.edu/", "ywcc", "YWCC", "njit"),
    ProseEntry("https://cs.njit.edu/", "computer-science", "Computer Science", "ywcc"),
    ProseEntry("https://informatics.njit.edu/", "informatics", "Informatics", "ywcc"),
    ProseEntry("https://datascience.njit.edu/", "data-science", "Data Science", "ywcc"),
]

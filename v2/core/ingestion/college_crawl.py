"""Generalizable college/department PROSE crawler (Crawling 2.1).

Pilot: YWCC. Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB. Reuses the eos_crawl / web_crawler DFS spine AS-IS (already host+path scoped via
same_scope). Adds: URL-path page typing, structured date capture, in-host people-page skip,
distinct created_by for reconcile isolation. Makes NO serving/gating/usage decisions.

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import json as _json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from v2.core.ingestion import entry_points as _ep

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
            data = _json.loads(tag.string or "")
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

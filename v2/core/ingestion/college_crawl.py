"""Generalizable college/department PROSE crawler (Crawling 2.1).

Pilot: YWCC. Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB. Reuses the eos_crawl / web_crawler DFS spine AS-IS (already host+path scoped via
same_scope). Adds: URL-path page typing, structured date capture, in-host people-page skip,
distinct created_by for reconcile isolation. Makes NO serving/gating/usage decisions.

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from v2.core.ingestion import entry_points as _ep

# People-page segments = the LAST path segment of each SUPPLEMENTARY_PATH (the in-host people
# listings explore.py owns). Single source of truth — can't drift from the people crawler.
_PEOPLE_SEGMENTS = frozenset(p.strip("/").split("/")[-1].lower() for p in _ep.SUPPLEMENTARY_PATHS)

# URL-path segments that mark a page kind (segment match, not substring).
_NEWS_SEGMENTS = ("news", "announcement", "announcements")
_EVENT_SEGMENTS = ("event", "events")


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.lower().split("/") if s]


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

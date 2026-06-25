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

# URL-path segments that mark a page kind (segment match, not substring).
_NEWS_SEGMENTS = ("news", "announcement", "announcements")
_EVENT_SEGMENTS = ("event", "events")


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.lower().split("/") if s]


def classify_type(url: str) -> str:
    """Mechanically type a page by URL path SEGMENT (not substring): /news,/announcement(s) →
    news; /event(s) → event; else policy. A 'newsletter-signup' page is policy (segment match)."""
    segs = _segments(url)
    if any(s in _NEWS_SEGMENTS for s in segs):
        return "news"
    if any(s in _EVENT_SEGMENTS for s in segs):
        return "event"
    return "policy"

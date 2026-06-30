"""The ONE shared prose-page identity for the day-1 rebuild.

`canonical_prose_url` is the single normalizer every prose engine (college_crawl / catalog_crawl /
www_crawl) uses to key a page, so the same URL never lands as two rows. It collapses scheme/host/
trailing-slash/fragment differences but KEEPS the query string (a query like ?audience=international
can address a genuinely-distinct page — collapsing it would lose content; spec §4.1, Codex#3).

`canonical_link` resolves a `node/<id>` ↔ clean-URL alias by EVIDENCE (the page's own
`<link rel="canonical">`), never by string guessing. A missing/offsite/ambiguous canonical → None, and
the caller falls back to the source URL (ambiguous aliases stay active; spec §4.2).

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §4.1/§4.2
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup


def canonical_prose_url(url: str) -> str:
    """Normalize a prose URL to its canonical identity: https + lowercased host + trailing slash
    stripped (root kept as '/') + fragment dropped + query KEPT. Idempotent."""
    p = urlsplit(url.strip())
    scheme = "https" if p.scheme in ("http", "https", "") else p.scheme
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, p.query, ""))


def canonical_link(html: str) -> str | None:
    """Return the page's `<link rel="canonical">` href IFF present and on an njit.edu host; else None.
    Used to collapse `node/<id>` ↔ clean-URL aliases by the page's OWN evidence (never guessed)."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup is just "no canonical"
        return None
    tag = soup.find("link", rel="canonical")
    href = (tag.get("href") if tag else "") or ""
    href = href.strip()
    if not href:
        return None
    host = urlsplit(href).netloc.lower()
    if not (host == "njit.edu" or host.endswith(".njit.edu")):
        return None
    return href

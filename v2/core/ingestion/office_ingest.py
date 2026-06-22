"""Hybrid office-prose ingest. Generic prose → chunk-embed as type='office_page' (live).
High-stakes procedural pages (OPT/CPT/I-20, deadlines, billing/$-amounts) → STAGED
(is_active=0, stakes='high') for human sign-off, never auto-live ungrounded. spec §4.3 [RA4]."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

from v2.core.ingestion.gsa_docs import upsert_doc_items
from v2.core.ingestion.web_crawler import is_non_html, normalize_url, same_site, scope_prefix

_HIGH_STAKES_URL = re.compile(
    r"\b(opt|cpt|i-?20|i-?765|sevis|visas?|tuition|billing|payment|refund|deadlines?|fees?)\b",
    re.I,
)
_DOLLAR = re.compile(r"\$\s?\d")
_SECTION_ROOT = re.compile(r"^/[a-z0-9][a-z0-9-]*/?$")   # exactly one path segment


def content_hash(text: str) -> str:
    """Stable sha256 of a page's cleaned text — the change-detection key."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def is_high_stakes(url: str, text: str) -> bool:
    if _HIGH_STAKES_URL.search(url or ""):
        return True
    if _DOLLAR.search(text or "") and re.search(r"due|deadline|pay|owe|balance", text or "", re.I):
        return True
    return False


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/") or "index"
    return "office/" + re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:70]


def discover_candidate_hubs(seed_url: str, html: str, registered_urls: set[str]) -> list[str]:
    """Same-host top-level section roots (/<segment>/) linked from this page that are NOT the
    seed's own scope and NOT already registered — candidate office hubs for gated activation.
    Pure (no I/O). Caller writes each via entry_point_store.upsert_candidate."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        return []
    seed_scope = scope_prefix(seed_url)
    reg = set(registered_urls)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        url = normalize_url(href, seed_url)
        if not url.startswith("http") or not same_site(seed_url, url) or is_non_html(url):
            continue
        path = urlparse(url).path or "/"
        if not _SECTION_ROOT.match(path):
            continue
        if path.startswith(seed_scope) or url in reg or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def ingest_office_page(conn: sqlite3.Connection, *, org_id: int, url: str, title: str,
                       text: str, entry_point_id: int | None = None) -> tuple[int, str]:
    """Returns (chunk_count, leg) with leg in {'chunk','staged','unchanged'}."""
    h = content_hash(text)
    prior = conn.execute("SELECT content_hash FROM office_page_state WHERE url=?", (url,)).fetchone()
    if prior is not None and prior[0] == h:
        conn.execute("UPDATE office_page_state SET last_seen_at=datetime('now') WHERE url=?", (url,))
        return 0, "unchanged"                          # no re-ingest, no embedding churn
    slug = _slug_from_url(url)
    if is_high_stakes(url, text):
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler",
                             is_active=0, stakes="high")
        leg = "staged"
    else:
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler",
                             is_active=1)
        leg = "chunk"
    conn.execute(
        "INSERT INTO office_page_state(url,entry_point_id,content_hash) VALUES(?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET content_hash=excluded.content_hash, "
        "entry_point_id=excluded.entry_point_id, last_seen_at=datetime('now')",
        (url, entry_point_id, h))
    return n, leg


def retire_404(conn, *, org_id, fetch, seen_urls):
    """Deactivate office_page docs that are CONFIRMED gone (HTTP 404/410). Source/type-scoped.
    NEVER retires on an empty crawl (seen_urls empty = transient failure) or a transport error
    (status None). spec §4.5 [SE3]."""
    if not seen_urls:
        return {"checked": 0, "retired": 0}
    existing = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_url FROM knowledge_items "
        "WHERE type='office_page' AND created_by='crawler' AND org_id=? AND is_active=1 "
        "AND source_url IS NOT NULL", (org_id,)).fetchall()]
    checked = retired = 0
    for url in existing:
        if url in seen_urls:
            continue                                    # just successfully crawled — alive
        checked += 1
        _html, status = fetch(url)
        if status in (404, 410):
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE source_url=? AND type='office_page' AND created_by='crawler' AND org_id=?", (url, org_id))
            conn.execute("DELETE FROM office_page_state WHERE url=?", (url,))
            retired += 1
    return {"checked": checked, "retired": retired}

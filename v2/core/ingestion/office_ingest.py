"""Hybrid office-prose ingest. Generic prose → chunk-embed as type='office_page' (live).
High-stakes procedural pages (OPT/CPT/I-20, deadlines, billing/$-amounts) → STAGED
(is_active=0, stakes='high') for human sign-off, never auto-live ungrounded. spec §4.3 [RA4]."""
from __future__ import annotations

import hashlib
import re
import sqlite3

from v2.core.ingestion.gsa_docs import upsert_doc_items

_HIGH_STAKES_URL = re.compile(r"opt|cpt|i-?20|i-?765|sevis|visa|deadline|tuition|bursar|"
                              r"billing|fee|payment|refund", re.I)
_DOLLAR = re.compile(r"\$\s?\d")


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
    tail = url.rstrip("/").rsplit("/", 1)[-1] or "index"
    return "office/" + re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")[:70]


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

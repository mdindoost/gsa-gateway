"""Hybrid office-prose ingest. Generic prose → chunk-embed as type='office_page' (live).
High-stakes procedural pages (OPT/CPT/I-20, deadlines, billing/$-amounts) → STAGED
(is_active=0, stakes='high') for human sign-off, never auto-live ungrounded. spec §4.3 [RA4]."""
from __future__ import annotations

import re
import sqlite3

from v2.core.ingestion.gsa_docs import upsert_doc_items

_HIGH_STAKES_URL = re.compile(r"opt|cpt|i-?20|i-?765|sevis|visa|deadline|tuition|bursar|"
                              r"billing|fee|payment|refund", re.I)
_DOLLAR = re.compile(r"\$\s?\d")


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
                       text: str) -> tuple[int, str]:
    """Returns (chunk_count, leg) with leg in {'chunk','staged'}."""
    slug = _slug_from_url(url)
    if is_high_stakes(url, text):
        n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                             source_url=url, doc_type="office_page", source="crawler",
                             is_active=0, stakes="high")
        return n, "staged"
    n = upsert_doc_items(conn, org_id=org_id, slug=slug, title=title, text=text,
                         source_url=url, doc_type="office_page", source="crawler",
                         is_active=1)
    return n, "chunk"

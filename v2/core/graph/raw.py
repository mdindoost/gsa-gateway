"""Verbatim page snapshots for change-detection (M2: hash the normalized structure,
not raw bytes). Phase 1a stores snapshots; the skip-re-extract-on-unchanged-hash
logic is wired in Phase 1b."""
from __future__ import annotations

import hashlib
import sqlite3

from bs4 import BeautifulSoup


def page_text(html: str) -> str:
    """Normalized visible text of a page (scripts/style/nav/footer/header stripped,
    whitespace collapsed). Used both for the change-detection hash and as the body of a
    'webpage' knowledge_item for unstructured pages (e.g. personal sites)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    return " ".join(soup.get_text(" ").split())


def struct_hash(html: str) -> str:
    """SHA-256 of the normalized text structure — comments / scripts / whitespace
    noise don't change it, real content does."""
    return hashlib.sha256(page_text(html).encode("utf-8")).hexdigest()


def save_raw_page(conn: sqlite3.Connection, url: str, content: str,
                  status: str = "ok") -> str:
    """Upsert a page snapshot; returns its structural hash (empty for non-ok status)."""
    h = struct_hash(content) if status == "ok" else ""
    conn.execute(
        "INSERT INTO raw_pages(url,content,struct_hash,status,fetched_at) "
        "VALUES(?,?,?,?,datetime('now')) "
        "ON CONFLICT(url) DO UPDATE SET content=excluded.content, "
        "struct_hash=excluded.struct_hash, status=excluded.status, "
        "fetched_at=datetime('now')",
        (url, content, h, status))
    return h

"""Turn a GSA prose doc (constitution, bylaws, travel-award info, …) into chunked
knowledge_items for the KB. Pure (text in, rows written). Chunking reuses the running
bot's tiktoken chunker so v1 and v2 chunk identically. source/created_by='dashboard'."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from v2.core.ingestion.section_chunker import chunk_markdown


def chunk_doc(text: str) -> list[str]:
    """Structure-aware chunking: one chunk per markdown section/subsection (heading path
    kept for context), so distinct facts (officer duties, Advisors, Prizes) don't share a
    chunk and compete at retrieval. Over-long sections are sub-split by paragraph."""
    return [c for c in chunk_markdown(text) if c.strip()]


def upsert_doc_items(conn: sqlite3.Connection, *, org_id: int, slug: str, title: str,
                     text: str, source_url: str | None, doc_type: str = "policy",
                     source: str = "dashboard", is_active: int = 1,
                     stakes: str | None = None) -> int:
    """(Re)ingest one doc: retire/clean any prior chunks for this doc slug, insert the new
    chunks as knowledge_items (one per chunk, shared metadata.doc_id so the retriever groups
    them). Returns the chunk count. NOT committed here — the CLI wrapper owns the transaction
    + backup.

    Staging (NJIT grad-content crawl): pass ``is_active=0`` to STAGE a doc (high-stakes facts
    held for human sign-off — invisible to retrieval, which only sees is_active=1) and
    ``stakes='high'`` to tag it; a later approve step flips is_active=1 and re-embeds. Default
    (is_active=1, stakes=None) is the unchanged live-ingest behavior."""
    doc_id = f"gsa-doc/{slug}"
    # (1) Hard-clean prior INACTIVE chunks from THIS source for this doc, so a re-run never
    # accumulates duplicate is_active=0 review-pool / retired rows (staged-idempotency fix).
    conn.execute(
        "DELETE FROM knowledge_items WHERE created_by=? AND is_active=0 "
        "AND json_extract(metadata,'$.doc_id')=?", (source, doc_id))
    # (2) Retire prior ACTIVE chunks for this doc — match new-style (doc_id) and legacy
    # (shared entity_id) metadata so re-ingest is idempotent across the entity_id change.
    conn.execute(
        "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
        "WHERE is_active=1 AND (json_extract(metadata,'$.doc_id')=? "
        "OR json_extract(metadata,'$.entity_id')=?)", (doc_id, doc_id))
    chunks = chunk_doc(text)
    for i, chunk in enumerate(chunks):
        # Per-section entity_id → each section is its own retrieval bucket, so distinct facts
        # in one doc (e.g. AirBNB / $900 / fiscal-year in the travel packet) can co-surface
        # instead of competing for a single per-doc slot under _diversify_and_expand. The
        # shared doc_id keeps re-ingest/retire doc-scoped.
        meta_d = {"entity_id": f"{doc_id}#{i}", "doc_id": doc_id, "verified": True,
                  "natural_key": f"{doc_id}:{doc_type}:{i}"}
        if stakes:
            meta_d["stakes"] = stakes
        meta = json.dumps(meta_d)
        # search_text is a GENERATED column (title || ' ' || content) — never insert it.
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
            "source_url,is_active,created_by) VALUES(?,?,?,?,?,1,?,?,?)",
            (org_id, doc_type, title, chunk, meta, source_url, is_active, source))
        conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                     (cur.lastrowid, cur.lastrowid))
    return len(chunks)

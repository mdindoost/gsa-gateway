"""Turn a GSA prose doc (constitution, bylaws, travel-award info, …) into chunked
knowledge_items for the KB. Pure (text in, rows written). Chunking reuses the running
bot's tiktoken chunker so v1 and v2 chunk identically. source/created_by='dashboard'."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bot.services.chunker import DocumentChunker

_CHUNKER = DocumentChunker(Path(__file__).resolve().parents[2] / "bot" / "data")


def chunk_doc(text: str) -> list[str]:
    """Split prose into <=350-token chunks (sentence-aware), via the shared chunker."""
    return [c for c in _CHUNKER.split_text_by_tokens(text) if c.strip()]


def upsert_doc_items(conn: sqlite3.Connection, *, org_id: int, slug: str, title: str,
                     text: str, source_url: str | None, doc_type: str = "policy") -> int:
    """(Re)ingest one doc: retire any prior active chunks for this doc slug, insert the new
    chunks as knowledge_items (one per chunk, shared metadata.entity_id='gsa-doc/<slug>' so
    the retriever groups them), created_by='dashboard'. Returns the chunk count. The caller
    embeds afterwards via v2/scripts/embed_all.py (resumable). NOT committed here — the CLI
    wrapper owns the transaction + backup."""
    entity_id = f"gsa-doc/{slug}"
    conn.execute(
        "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
        "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=?", (entity_id,))
    chunks = chunk_doc(text)
    for i, chunk in enumerate(chunks):
        meta = json.dumps({"entity_id": entity_id, "verified": True,
                           "natural_key": f"{entity_id}:{doc_type}:{i}"})
        # search_text is a GENERATED column (title || ' ' || content) — never insert it.
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
            "source_url,is_active,created_by) VALUES(?,?,?,?,?,1,?,1,'dashboard')",
            (org_id, doc_type, title, chunk, meta, source_url))
        conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                     (cur.lastrowid, cur.lastrowid))
    return len(chunks)

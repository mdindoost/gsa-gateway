"""Populate (and invalidate) the chunk + chunk-vector tables for a knowledge item.

`populate_item_chunks` is the single reusable unit every writer calls to keep chunks in
sync: it ALWAYS drops the item's existing chunks first (invalidation), then — only if the
item is active — re-chunks, embeds, and writes. A superseded/missing item ends up with zero
chunks, which the vector GC + invariant rely on. The embed function is injected
(provider-isolated), so the real pipeline passes an Ollama-backed embedder and tests pass a
fake one (no Ollama needed). Callers own the transaction (no commit here).
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from typing import Callable, Optional

import sqlite_vec

from v2.core.retrieval.chunker import chunk_text
from v2.core.retrieval.model_descriptor import ModelDescriptor

# text -> raw (un-normalized) embedding, or None on failure
EmbedFn = Callable[[str], Optional[list]]


def _normalize(vec: list) -> Optional[list]:
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec] if n else None


def content_hash(text: str, model_id: str) -> str:
    """Hash of chunk text + model id — changes when content OR model changes."""
    return hashlib.sha256(f"{model_id}\x00{text}".encode("utf-8")).hexdigest()


def drop_item_chunks(conn: sqlite3.Connection, item_id: int) -> int:
    """Delete an item's chunks and their vectors. Returns # chunks dropped."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM knowledge_chunks WHERE parent_id = ?", (item_id,))]
    conn.executemany("DELETE FROM knowledge_chunk_vectors WHERE chunk_id = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM knowledge_chunks WHERE parent_id = ?", (item_id,))
    return len(ids)


def populate_item_chunks(conn: sqlite3.Connection, item_id: int,
                         embed_fn: EmbedFn, descriptor: ModelDescriptor) -> int:
    """Invalidate + (re)build chunks/vectors for one item. Returns # vectors written."""
    drop_item_chunks(conn, item_id)
    row = conn.execute(
        "SELECT org_id, type, content, is_active FROM knowledge_items WHERE id = ?",
        (item_id,)).fetchone()
    if row is None or row["is_active"] != 1:
        return 0
    org_id, typ, content = row["org_id"], row["type"], row["content"]
    source_key = f"item:{item_id}"
    written = 0
    for ordinal, chunk in enumerate(chunk_text(content or "", descriptor)):
        cur = conn.execute(
            "INSERT INTO knowledge_chunks(parent_id, source_key, ordinal, text, content_hash, model_id) "
            "VALUES (?,?,?,?,?,?)",
            (item_id, source_key, ordinal, chunk, content_hash(chunk, descriptor.id), descriptor.id),
        )
        chunk_id = cur.lastrowid
        embed_input = descriptor.doc_prefix + descriptor.truncate_to_tokens(chunk, descriptor.context_window)
        norm = _normalize(embed_fn(embed_input) or [])
        if norm is None:
            continue
        conn.execute(
            "INSERT INTO knowledge_chunk_vectors(chunk_id, embedding, org_id, type, parent_id) "
            "VALUES (?,?,?,?,?)",
            (chunk_id, sqlite_vec.serialize_float32(norm), org_id, typ, item_id),
        )
        written += 1
    return written

"""Vector garbage collection: delete vectors whose parent item is gone or superseded.

The system SOFT-deletes knowledge_items (is_active=0 + a new row), so FK cascade
never fires and vectors orphan by inactivity. This is the GC net (and the invariant
that enforces it). Works for both the per-item knowledge_vectors and the per-chunk
knowledge_chunk_vectors. Callers own the transaction (these do not commit).
"""
from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v2.core.retrieval.model_descriptor import ModelDescriptor

_ITEM_ORPHANS = """
    SELECT v.item_id FROM knowledge_vectors v
    LEFT JOIN knowledge_items i ON i.id = v.item_id AND i.is_active = 1
    WHERE i.id IS NULL
"""
_CHUNK_ORPHANS = """
    SELECT cv.chunk_id FROM knowledge_chunk_vectors cv
    LEFT JOIN knowledge_chunks c ON c.id = cv.chunk_id
    LEFT JOIN knowledge_items i ON i.id = c.parent_id AND i.is_active = 1
    WHERE i.id IS NULL
"""


def count_orphan_item_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({_ITEM_ORPHANS})").fetchone()[0]


def sweep_orphan_item_vectors(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(_ITEM_ORPHANS)]
    conn.executemany("DELETE FROM knowledge_vectors WHERE item_id = ?", [(i,) for i in ids])
    return len(ids)


def count_orphan_chunk_vectors(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({_CHUNK_ORPHANS})").fetchone()[0]


def sweep_orphan_chunk_vectors(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute(_CHUNK_ORPHANS)]
    conn.executemany("DELETE FROM knowledge_chunk_vectors WHERE chunk_id = ?", [(i,) for i in ids])
    return len(ids)


def assert_no_orphans(conn: sqlite3.Connection) -> None:
    item = count_orphan_item_vectors(conn)
    chunk = count_orphan_chunk_vectors(conn)
    assert item == 0 and chunk == 0, f"orphan vectors present: item={item} chunk={chunk}"


# ── Chunk-invariant helpers ──────────────────────────────────────────────────

def _chunk_vector_schema_dim(conn: sqlite3.Connection) -> int | None:
    """Parse the embedding dimension from the knowledge_chunk_vectors DDL.

    The vec0 virtual-table schema encodes the width as ``FLOAT[<N>]``; we
    extract that N so the invariant can verify the table was built for the
    active descriptor without needing a live embedding to compare against.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'knowledge_chunk_vectors'"
    ).fetchone()
    if row is None:
        return None
    m = re.search(r"FLOAT\[(\d+)\]", row[0], re.IGNORECASE)
    return int(m.group(1)) if m else None


def _count_chunks_without_vectors(conn: sqlite3.Connection) -> int:
    """Count chunks (with an active parent) that have NO entry in knowledge_chunk_vectors.

    Scoped to active-parent chunks only: after a GC sweep removes an inactive item's
    vectors but leaves its orphan chunks, counting those would false-fire condition 2.
    """
    return conn.execute(
        """
        SELECT COUNT(*) FROM knowledge_chunks c
        JOIN knowledge_items i ON i.id = c.parent_id
        WHERE i.is_active = 1
          AND NOT EXISTS (
            SELECT 1 FROM knowledge_chunk_vectors cv WHERE cv.chunk_id = c.id
          )
        """
    ).fetchone()[0]


def _count_served_items_without_current_chunks(
    conn: sqlite3.Connection, model_id: str, exclude_types: frozenset[str]
) -> int:
    """Count active served items that have no chunk for the active model_id.

    'Served' means is_active=1 AND type NOT IN exclude_types — exactly the
    set the retriever's DEFAULT_EXCLUDE_TYPES definition covers.
    """
    placeholders = ",".join("?" * len(exclude_types))
    sql = f"""
        SELECT COUNT(*) FROM knowledge_items i
        WHERE i.is_active = 1
          AND i.type NOT IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM knowledge_chunks c
              WHERE c.parent_id = i.id AND c.model_id = ?
          )
    """
    return conn.execute(sql, (*exclude_types, model_id)).fetchone()[0]


def _count_stale_model_chunks(conn: sqlite3.Connection, model_id: str) -> int:
    """Count chunks with a model_id that differs from the active descriptor."""
    return conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE model_id != ?", (model_id,)
    ).fetchone()[0]


# ── Public API ────────────────────────────────────────────────────────────────

def assert_chunk_invariant(conn: sqlite3.Connection, descriptor: "ModelDescriptor") -> None:
    """Raise AssertionError unless the chunk index is fully coherent.

    Conditions checked (all must hold):
    1. Every active *served* item (is_active=1, type not in DEFAULT_EXCLUDE_TYPES)
       has at least one chunk with ``model_id == descriptor.id``.
    2. Every chunk has a corresponding row in knowledge_chunk_vectors (no un-embedded
       chunks).
    3. Every chunk-vector's parent item is active (no orphan vectors) — delegates to
       ``assert_no_orphans``.
    4. No chunk exists with ``model_id != descriptor.id`` (stale-model rows must be
       swept before the corpus is considered ready).
    5. The knowledge_chunk_vectors table's embedding column width equals
       ``descriptor.dim`` (the vec0 DDL matches the active descriptor).
    """
    # Import here to avoid a circular import at module load time; the retriever
    # imports from schema, not from vector_gc, so this is safe.
    from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES  # noqa: PLC0415

    # 1. Active served items must have at least one current-model chunk.
    uncovered = _count_served_items_without_current_chunks(
        conn, descriptor.id, DEFAULT_EXCLUDE_TYPES
    )
    assert uncovered == 0, (
        f"{uncovered} active served item(s) have no chunk with model_id={descriptor.id!r}"
    )

    # 2. Every chunk must have a vector.
    unvectored = _count_chunks_without_vectors(conn)
    assert unvectored == 0, (
        f"{unvectored} chunk(s) have no entry in knowledge_chunk_vectors"
    )

    # 3. No orphan vectors (item-level and chunk-level).
    assert_no_orphans(conn)

    # 4. No chunks carrying a stale model_id.
    stale = _count_stale_model_chunks(conn, descriptor.id)
    assert stale == 0, (
        f"{stale} chunk(s) carry stale model_id (expected {descriptor.id!r})"
    )

    # 5. Vec0 DDL dimension must match the descriptor.
    schema_dim = _chunk_vector_schema_dim(conn)
    assert schema_dim == descriptor.dim, (
        f"knowledge_chunk_vectors embedding dim={schema_dim} "
        f"does not match descriptor.dim={descriptor.dim}"
    )


def corpus_build_ready(conn: sqlite3.Connection, descriptor: "ModelDescriptor") -> bool:
    """Return True iff assert_chunk_invariant passes without raising."""
    try:
        assert_chunk_invariant(conn, descriptor)
        return True
    except AssertionError:
        return False

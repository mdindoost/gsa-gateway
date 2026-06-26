"""Vector garbage collection: delete vectors whose parent item is gone or superseded.

The system SOFT-deletes knowledge_items (is_active=0 + a new row), so FK cascade
never fires and vectors orphan by inactivity. This is the GC net (and the invariant
that enforces it). Works for both the per-item knowledge_vectors and the per-chunk
knowledge_chunk_vectors. Callers own the transaction (these do not commit).
"""
from __future__ import annotations

import sqlite3

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

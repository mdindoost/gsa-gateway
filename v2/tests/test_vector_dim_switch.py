"""vec0 embedding dimension is descriptor-driven, and a helper can recreate the vector
tables at a new dim (the nomic-768 -> qwen-1024 migration for the rebuilt DB)."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sqlite_vec

from v2.core.database.schema import (
    create_all, get_connection, recreate_vector_tables, vector_table_ddl,
)
from v2.core.retrieval.model_descriptor import NOMIC, QWEN


def _insert_ok(conn, table, col, id_, vec) -> bool:
    try:
        conn.execute(f"INSERT INTO {table}({col}, embedding) VALUES (?, ?)",
                     (id_, sqlite_vec.serialize_float32(vec)))
        return True
    except Exception:       # noqa: BLE001 - sqlite-vec raises on dim mismatch
        return False


def test_vector_table_ddl_uses_descriptor_dim():
    ddl = "\n".join(vector_table_ddl(QWEN))
    assert "FLOAT[1024]" in ddl
    assert "FLOAT[768]" not in ddl
    nomic_ddl = "\n".join(vector_table_ddl(NOMIC))
    assert "FLOAT[768]" in nomic_ddl


def test_create_all_builds_qwen_dim(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    conn = create_all(str(tmp_path / "q.db"))
    assert _insert_ok(conn, "knowledge_vectors", "item_id", 1, [0.1] * 1024)
    assert not _insert_ok(conn, "knowledge_vectors", "item_id", 2, [0.1] * 768)


def test_recreate_vector_tables_migrates_768_to_1024(tmp_path):
    # Built at nomic (autouse pins nomic -> 768).
    db = str(tmp_path / "m.db")
    conn = create_all(db)
    assert _insert_ok(conn, "knowledge_vectors", "item_id", 1, [0.1] * 768)
    # Migrate the vec0 tables to qwen's 1024.
    recreate_vector_tables(conn, QWEN)
    # Old vectors are gone (tables recreated empty) and 1024-d now fits, 768-d does not.
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0] == 0
    assert _insert_ok(conn, "knowledge_vectors", "item_id", 1, [0.1] * 1024)
    assert not _insert_ok(conn, "knowledge_vectors", "item_id", 2, [0.1] * 768)


def test_recreate_defaults_to_active_descriptor(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    conn = create_all(str(tmp_path / "d.db"))     # qwen -> 1024 already
    recreate_vector_tables(conn)                   # no descriptor -> active (qwen)
    assert _insert_ok(conn, "knowledge_vectors", "item_id", 1, [0.1] * 1024)

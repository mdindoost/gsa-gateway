from __future__ import annotations
import sqlite3, sqlite_vec

def get_ro_connection(db_path: str = "gsa_gateway.db") -> sqlite3.Connection:
    """TRUE read-only handle (not RW-by-discipline). Reads/FTS/vec work; accidental writes impossible."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn

from __future__ import annotations
import hashlib, shutil, sqlite3
from pathlib import Path

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def make_snapshot(prod_db: str, snapshot_dir: str) -> tuple[str, str]:
    """Copy the production DB to snapshot_dir/snap_<shorthash>.db. Returns (path, full_sha256).
    The hash is computed AFTER copy so it identifies the exact frozen bytes Kavosh ran against."""
    Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
    src_hash = _sha256(prod_db)
    dest = str(Path(snapshot_dir) / f"snap_{src_hash[:12]}.db")
    shutil.copyfile(prod_db, dest)
    return dest, _sha256(dest)

def ro_connect(db_path: str) -> sqlite3.Connection:
    """Read-only connection for ground-truth reads. sqlite-vec loaded so vec tables are visible;
    pure SELECTs on nodes/edges/knowledge_items don't need it but loading is harmless."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec  # noqa
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass  # ground-truth SELECTs don't require vec
    return conn

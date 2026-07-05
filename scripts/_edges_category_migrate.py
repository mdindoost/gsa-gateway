#!/usr/bin/env python
"""General migration: widen the ``edges.category`` CHECK constraint to the current
schema's allowed set. Historically added 'officer'/'deprep' (GSA officer / DepRep roles);
now also 'affiliated' (cross-listing / courtesy faculty — see the 2026-07-05 affiliated-
faculty design). Keep this as THE one widen-the-edges-CHECK migration — extend the CHECK +
the ``needs_migration`` sentinel when a new category is added to ``schema.py::EDGES``.

SQLite cannot ALTER a CHECK constraint, so this rebuilds the STRICT ``edges`` table the
documented way — create new table → copy rows → drop old → rename → recreate indexes —
inside one transaction with ``foreign_key_check``. ``edges`` has no inbound FKs and no
triggers (verified), so the rebuild is self-contained.

Idempotent: if the constraint already allows the newest category ('affiliated'), it does
nothing. Dry-run by default; ``--commit`` takes a hardened backup first.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

# Mirror of v2.core.database.schema.EDGES with the widened CHECK (named edges_new).
EDGES_NEW = """
CREATE TABLE edges_new (
    id               INTEGER PRIMARY KEY,
    src_id           INTEGER NOT NULL REFERENCES nodes(id),
    type             TEXT NOT NULL,
    dst_id           INTEGER NOT NULL REFERENCES nodes(id),
    category         TEXT,
    area_source      TEXT,
    source_section   TEXT,
    attrs            TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    source_doc_id    INTEGER,
    ontology_version INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (category IS NULL OR category IN
           ('faculty','staff','admin','advisor','joint','emeritus','officer','deprep','affiliated'))
) STRICT;
"""
_COLS = ("id,src_id,type,dst_id,category,area_source,source_section,attrs,source,"
         "source_doc_id,ontology_version,is_active,created_at,updated_at")
_INDEXES = [
    "CREATE UNIQUE INDEX idx_edges_triple ON edges(src_id, type, dst_id)",
    "CREATE INDEX idx_edges_src   ON edges(src_id, is_active)",
    "CREATE INDEX idx_edges_dst   ON edges(dst_id, type, is_active)",
]


def needs_migration(conn: sqlite3.Connection) -> bool:
    """True iff the live edges table's CHECK does not yet allow the newest category
    ('affiliated'). The sentinel is always the most-recently-added value — the live DB
    already contains 'officer'/'deprep', so testing those would no-op forever."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'").fetchone()
    return bool(row) and "'affiliated'" not in row[0]


def migrate(conn: sqlite3.Connection) -> None:
    """Rebuild ``edges`` with the widened CHECK, preserving every row and recreating the
    three indexes. Caller has already taken a hardened backup. Runs in autocommit so the
    PRAGMA/BEGIN/COMMIT sequence behaves; rolls back and raises on any FK violation."""
    conn.isolation_level = None                 # explicit transaction control
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("DROP TABLE IF EXISTS edges_new")   # self-heal a prior aborted run
        conn.execute(EDGES_NEW)
        conn.execute(f"INSERT INTO edges_new ({_COLS}) SELECT {_COLS} FROM edges")
        conn.execute("DROP TABLE edges")
        conn.execute("ALTER TABLE edges_new RENAME TO edges")
        for ix in _INDEXES:
            conn.execute(ix)
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            conn.execute("ROLLBACK")
            raise RuntimeError(f"foreign_key_check failed, rolled back: {bad}")
        conn.execute("COMMIT")
    except Exception:
        # best-effort rollback if we failed before COMMIT
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    before = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    if not needs_migration(conn):
        print(f"edges.category already allows 'affiliated' — nothing to do "
              f"({before} rows).")
        return 0
    print(f"edges needs CHECK widening: {before} rows to preserve, 3 indexes to recreate.")
    if not args.commit:
        print("(dry run — pass --commit to rebuild; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-edges-category")
    print(f"backup: {bkp.name}")
    migrate(conn)
    after = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    if after != before:
        raise RuntimeError(f"row count changed during rebuild: {before} -> {after}")
    print(f"committed: edges rebuilt, {after} rows preserved, indexes recreated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

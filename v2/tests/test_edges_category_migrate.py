from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sqlite3
import pytest
from scripts._edges_category_migrate import needs_migration, migrate

# The OLD edges table (pre-officer/deprep), as the live DB has it.
OLD_EDGES = """
CREATE TABLE edges (
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
           ('faculty','staff','admin','advisor','joint','emeritus'))
) STRICT;
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT) STRICT")
    c.execute(OLD_EDGES)
    c.execute("CREATE UNIQUE INDEX idx_edges_triple ON edges(src_id, type, dst_id)")
    c.execute("CREATE INDEX idx_edges_src ON edges(src_id, is_active)")
    c.execute("CREATE INDEX idx_edges_dst ON edges(dst_id, type, is_active)")
    c.execute("INSERT INTO nodes(id,name) VALUES(1,'p'),(2,'o')")
    c.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
              "VALUES(1,'has_role',2,'faculty','crawler')")
    c.commit()
    yield c
    c.close()


def test_old_table_needs_migration_and_rejects_officer(conn):
    assert needs_migration(conn) is True
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                     "VALUES(1,'has_role',2,'officer','dashboard')")


def test_migrate_preserves_rows_indexes_and_allows_officer(conn):
    before = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    migrate(conn)
    # rows preserved
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == before
    assert conn.execute("SELECT category FROM edges WHERE src_id=1").fetchone()[0] == "faculty"
    # constraint now allows officer/deprep
    assert needs_migration(conn) is False
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                 "VALUES(2,'has_role',1,'officer','dashboard')")
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                 "VALUES(1,'advises',2,'deprep','dashboard')")
    # still rejects a bogus category
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                     "VALUES(2,'x',2,'bogus','dashboard')")
    # the three indexes are back
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='edges'")}
    assert {"idx_edges_triple", "idx_edges_src", "idx_edges_dst"} <= idx


def test_migrate_is_idempotent(conn):
    migrate(conn)
    assert needs_migration(conn) is False   # second run would be a no-op at the CLI layer

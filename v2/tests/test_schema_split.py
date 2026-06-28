"""Tests for the schema split into knowledge vs ops builders (Phase 1)."""

import sqlite3
import tempfile
import os

from v2.core.database import schema


MOVED = {
    "posts", "post_templates", "post_deliveries", "events", "event_reminders",
    "judging_events", "judging_judges", "judging_presenters", "judging_scores",
    "judging_audience_votes", "judging_score_audit",
}


def _tables(path):
    c = sqlite3.connect(path)
    rows = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    return rows


def _cols(path, table):
    c = sqlite3.connect(path)
    info = c.execute(f"PRAGMA table_info({table})").fetchall()
    c.close()
    return {row[1]: row for row in info}   # name -> (cid,name,type,notnull,dflt,pk)


# ─── Task 1: schema partition ────────────────────────────────────────────────

def test_knowledge_schema_has_no_moved_tables(tmp_path):
    p = str(tmp_path / "k.db")
    schema.create_knowledge_schema(p).close()
    assert _tables(p).isdisjoint(MOVED)            # HIGH-3 invariant
    assert "knowledge_items" in _tables(p) and "organizations" in _tables(p)


def test_ops_schema_has_exactly_moved_tables(tmp_path):
    p = str(tmp_path / "o.db")
    schema.create_ops_schema(p).close()
    assert MOVED.issubset(_tables(p))
    assert "knowledge_items" not in _tables(p) and "nodes" not in _tables(p)


# ─── Task 2: org_slug + live events shape ────────────────────────────────────

def test_ops_posts_events_templates_have_org_slug(tmp_path):
    p = str(tmp_path / "o.db")
    schema.create_ops_schema(p).close()
    for t in ("posts", "events", "post_templates"):
        cols = _cols(p, t)
        assert "org_slug" in cols and cols["org_slug"][3] == 1   # NOT NULL
        assert "org_id" in cols                                    # retained


def test_ops_events_is_live_shape(tmp_path):
    p = str(tmp_path / "o.db")
    schema.create_ops_schema(p).close()
    cols = _cols(p, "events")
    # Legacy columns preserved from v1 live shape
    assert {"announcement_sent", "channel_posted"} <= set(cols)


def test_ops_events_has_autoincrement(tmp_path):
    """AUTOINCREMENT events registers in sqlite_sequence after first insert."""
    p = str(tmp_path / "o.db")
    schema.create_ops_schema(p).close()
    c = sqlite3.connect(p)
    # Insert an event to trigger sqlite_sequence registration
    c.execute(
        "INSERT INTO events (name, date, org_slug) VALUES ('Test Event', '2026-07-01', 'gsa')"
    )
    c.commit()
    seq = c.execute("SELECT name FROM sqlite_sequence WHERE name='events'").fetchone()
    c.close()
    assert seq is not None, "events should be AUTOINCREMENT (registered in sqlite_sequence)"


def test_ops_posts_no_org_fk(tmp_path):
    """posts.org_id must be a plain INTEGER, not a FK to organizations."""
    p = str(tmp_path / "o.db")
    schema.create_ops_schema(p).close()
    c = sqlite3.connect(p)
    # PRAGMA foreign_key_list returns rows only if there are FK constraints
    fk_rows = c.execute("PRAGMA foreign_key_list(posts)").fetchall()
    c.close()
    # org_id should NOT appear as a FK source
    fk_from_cols = {r[3] for r in fk_rows}  # column [3] is "from" column name
    assert "org_id" not in fk_from_cols, "posts.org_id must not be a foreign key"


# ─── Task 3: operations_db_path config ───────────────────────────────────────

def test_operations_db_path_default_sibling():
    """operations_db_path defaults to a sibling of database_path."""
    from bot.config import load_config
    import os
    # Clear OPERATIONS_DB_PATH so we get the default
    os.environ.pop("OPERATIONS_DB_PATH", None)
    cfg = load_config()
    # Default database_path = ./gsa_gateway.db; ops should be ./gsa_gateway_ops.db
    assert cfg.operations_db_path.endswith("gsa_gateway_ops.db")


def test_operations_db_path_env_override(monkeypatch):
    """OPERATIONS_DB_PATH env var overrides the default."""
    from bot.config import load_config
    monkeypatch.setenv("OPERATIONS_DB_PATH", "/tmp/my_ops.db")
    cfg = load_config()
    assert cfg.operations_db_path == "/tmp/my_ops.db"


# ─── Task 4: startup keeps moved tables out of knowledge ─────────────────────

def test_server_startup_keeps_moved_tables_out_of_knowledge(tmp_path):
    """Simulates the server's new startup: knowledge schema on DB_PATH, ops on OPS_PATH.
    After create_knowledge_schema, the knowledge DB must have none of the MOVED tables.
    """
    kb_path = str(tmp_path / "knowledge.db")
    ops_path = str(tmp_path / "ops.db")
    schema.create_knowledge_schema(kb_path).close()
    schema.create_ops_schema(ops_path).close()
    # Core invariant: knowledge DB has NO moved tables
    assert _tables(kb_path).isdisjoint(MOVED)
    # Ops DB has ALL moved tables
    assert MOVED.issubset(_tables(ops_path))
    # Knowledge DB still has the knowledge tables
    assert "knowledge_items" in _tables(kb_path)
    assert "organizations" in _tables(kb_path)

"""Build-4 split-ops tests: /db-ops endpoint serves an OPS snapshot,
/db serves a KB snapshot; both contain the right tables and exclude the wrong ones.

IMPORTANT: All tests use temp-file DBs only. No live-DB writes.

Test-harness gotcha (see plan): the existing `server` fixture is broken due to the
_host_ok() guard rejecting random-port Host headers. We bypass it by monkeypatching
BOTH ls.DB_PATH, ls.OPS_DB_PATH, AND ls.ALLOWED_HOSTS so our test host:port passes.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
from http.server import HTTPServer
from threading import Thread

import pytest

from v2.core.database.schema import create_knowledge_schema, create_ops_schema


# ── helper: check which tables a SQLite byte-blob has ────────────────────────

def _tables_in_bytes(data: bytes) -> set[str]:
    """Write bytes to a temp file, open with sqlite3, return set of table names."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    try:
        tmp.write(data)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    finally:
        os.unlink(tmp.name)


# ── fixture: live HTTP server with monkeypatched paths ───────────────────────

@pytest.fixture()
def db_ops_server(tmp_path, monkeypatch):
    """Spin up a real GatewayHandler HTTP server with temp KB + OPS DBs.

    Monkeypatches ls.DB_PATH, ls.OPS_DB_PATH, and ls.ALLOWED_HOSTS so:
    - the host-guard (_host_ok) passes for our bound address, and
    - the handler reads from isolated temp DBs (not the live production DB).
    """
    import v2.local_server as ls

    # Build KB temp DB
    kb_path = str(tmp_path / "kb.db")
    ops_path = str(tmp_path / "ops.db")
    kb_conn = create_knowledge_schema(kb_path)
    kb_conn.close()
    ops_conn = create_ops_schema(ops_path)
    ops_conn.close()

    # Monkeypatch module-level paths and allowed hosts
    monkeypatch.setattr(ls, "DB_PATH", __import__("pathlib").Path(kb_path))
    monkeypatch.setattr(ls, "OPS_DB_PATH", __import__("pathlib").Path(ops_path))

    # Spin up the server on a random port
    server = HTTPServer(("127.0.0.1", 0), ls.GatewayHandler)
    port = server.server_address[1]

    # Patch ALLOWED_HOSTS to include our random-port address
    monkeypatch.setattr(
        ls, "ALLOWED_HOSTS",
        {f"127.0.0.1:{port}", "localhost", "127.0.0.1"},
    )

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {"base": f"http://127.0.0.1:{port}", "port": port,
           "kb_path": kb_path, "ops_path": ops_path}

    server.shutdown()


# ── Task 1 tests ──────────────────────────────────────────────────────────────

def test_db_ops_endpoint_returns_sqlite_header(db_ops_server):
    """/db-ops returns bytes starting with the SQLite file header."""
    import urllib.request
    url = db_ops_server["base"] + "/db-ops"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{db_ops_server['port']}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    assert data[:16] == b"SQLite format 3\x00", (
        f"Expected SQLite header, got {data[:16]!r}"
    )


def test_db_ops_endpoint_contains_posts_table(db_ops_server):
    """/db-ops snapshot contains the OPS 'posts' table."""
    import urllib.request
    url = db_ops_server["base"] + "/db-ops"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{db_ops_server['port']}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    tables = _tables_in_bytes(data)
    assert "posts" in tables, f"Expected 'posts' in OPS snapshot, got tables: {tables}"


def test_db_ops_endpoint_does_not_contain_knowledge_items(db_ops_server):
    """/db-ops snapshot does NOT contain the KB-only 'knowledge_items' table."""
    import urllib.request
    url = db_ops_server["base"] + "/db-ops"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{db_ops_server['port']}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    tables = _tables_in_bytes(data)
    assert "knowledge_items" not in tables, (
        f"'knowledge_items' must NOT be in OPS snapshot, but found tables: {tables}"
    )


def test_db_endpoint_contains_knowledge_items(db_ops_server):
    """/db snapshot contains the KB 'knowledge_items' table."""
    import urllib.request
    url = db_ops_server["base"] + "/db"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{db_ops_server['port']}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    tables = _tables_in_bytes(data)
    assert "knowledge_items" in tables, (
        f"Expected 'knowledge_items' in KB snapshot, got tables: {tables}"
    )


def test_db_endpoint_does_not_contain_posts(db_ops_server):
    """/db snapshot does NOT contain the OPS 'posts' table."""
    import urllib.request
    url = db_ops_server["base"] + "/db"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{db_ops_server['port']}"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    tables = _tables_in_bytes(data)
    assert "posts" not in tables, (
        f"'posts' must NOT be in KB snapshot, but found tables: {tables}"
    )

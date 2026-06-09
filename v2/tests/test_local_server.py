"""Tests for v2/local_server.py — runs the real server against a temp db."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import v2.local_server as ls
from v2.core.database.schema import create_all


def _req(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    dbp = tmp_path / "test.db"
    conn = create_all(str(dbp))
    njit = conn.execute("INSERT INTO organizations(name,slug,type) VALUES('NJIT','njit','university')").lastrowid
    gsa = conn.execute("INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,?,?,?)",
                       (njit, "GSA", "gsa", "gsa")).lastrowid
    conn.execute("INSERT INTO settings(org_id,key,value,type) VALUES(?,?,?,?)",
                 (njit, "org.timezone", "America/New_York", "string"))
    conn.commit(); conn.close()

    monkeypatch.setattr(ls, "DB_PATH", dbp)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ls.GatewayHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{port}", "db": dbp, "gsa": gsa}
    httpd.shutdown(); httpd.server_close()


def test_health_endpoint(server):
    status, d = _req(server["url"] + "/health")
    assert status == 200 and d["status"] == "ok" and d["db_exists"] is True


def test_create_post(server):
    status, d = _req(server["url"] + "/posts", "POST",
                     {"org_id": server["gsa"], "type": "one_time", "content": "hi",
                      "channels": ["discord"]})
    assert status == 200 and d["success"] and d["post_id"]
    c = sqlite3.connect(str(server["db"]))
    assert c.execute("SELECT COUNT(*) FROM posts WHERE id=?", (d["post_id"],)).fetchone()[0] == 1
    c.close()


def test_create_event_creates_reminders(server):
    status, d = _req(server["url"] + "/posts", "POST", {
        "org_id": server["gsa"], "type": "event", "name": "Mixer", "date": "2026-07-01",
        "time": "18:00", "channels": ["discord"],
        "reminders": [{"offset": 7, "unit": "days"}, {"offset": 1, "unit": "days"}, {"offset": 1, "unit": "hours"}],
    })
    assert status == 200 and d["success"] and d["reminders"] == 3
    c = sqlite3.connect(str(server["db"]))
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM event_reminders").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM posts WHERE type='event_announcement'").fetchone()[0] == 1
    c.close()


def test_create_knowledge_item(server):
    status, d = _req(server["url"] + "/knowledge", "POST",
                     {"org_id": server["gsa"], "type": "faq", "title": "Q", "content": "A"})
    assert status == 200 and d["success"] and d["needs_reindex"] is True


def test_invalid_post_rejected(server):
    status, d = _req(server["url"] + "/posts", "POST",
                     {"org_id": server["gsa"], "type": "one_time"})  # missing content
    assert status == 400 and d["success"] is False


def test_server_binds_localhost_only():
    assert ls.HOST == "127.0.0.1"
    assert ls.HOST != "0.0.0.0"

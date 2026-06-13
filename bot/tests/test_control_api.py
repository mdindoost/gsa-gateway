"""Integration tests for the control-plane HTTP routes in v2/local_server.py.

Starts a real ThreadingHTTPServer on an ephemeral port against a temp DB, with
JOBS swapped for one that spawns trivial subprocesses (no Ollama/network). Covers
the routing and — importantly — the browser-attack guards (Host allowlist + the
X-GSA-Dashboard CSRF header + Origin check).
"""

import http.client
import json
import sqlite3
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import v2.local_server as srv
from bot.services.jobs import JobManager


def _fast_ok_builder(job_type, args):
    return [sys.executable, "-c", "print('job-ran')"]


def _slow_builder(job_type, args):
    return [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def server(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    sqlite3.connect(str(db_path)).close()  # so DB_PATH.exists() is true

    jobs = JobManager(db_path=str(db_path), repo_root=str(tmp_path),
                      python_bin=sys.executable,
                      jobs_log_dir=str(tmp_path / "logs" / "jobs"),
                      build_cmd=_fast_ok_builder)
    jobs.ensure_schema()

    monkeypatch.setattr(srv, "DB_PATH", db_path)
    monkeypatch.setattr(srv, "JOBS", jobs)
    monkeypatch.setattr(srv, "_ollama_up", lambda: True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.GatewayHandler)
    port = httpd.server_address[1]
    monkeypatch.setattr(srv, "ALLOWED_HOSTS",
                        {f"127.0.0.1:{port}", f"localhost:{port}"})
    monkeypatch.setattr(srv, "ALLOWED_ORIGINS",
                        {f"http://127.0.0.1:{port}", f"http://localhost:{port}", "null"})
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield srv, jobs, port
    httpd.shutdown()


def _request(port, method, path, *, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    payload = json.dumps(body) if body is not None else None
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
    conn.putrequest(method, path, skip_host=bool(host))
    if host:
        conn.putheader("Host", host)
    if payload is not None:
        hdrs["Content-Length"] = str(len(payload))
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders()
    if payload is not None:
        conn.send(payload.encode())
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    parsed = json.loads(data) if data else None
    return resp.status, parsed


CSRF = {"X-GSA-Dashboard": "1"}


def _wait_status(port, job_id, want, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _request(port, "GET", f"/api/jobs/{job_id}")
        if body and body.get("status") == want:
            return body
        time.sleep(0.05)
    return None


# ── health ────────────────────────────────────────────────────────────────────

def test_health_reports_ok_and_no_running_job(server):
    _, _, port = server
    status, body = _request(port, "GET", "/api/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["ollama"] is True
    assert body["running_job"] is None


# ── guards ────────────────────────────────────────────────────────────────────

def test_refresh_without_csrf_header_is_forbidden(server):
    _, _, port = server
    status, _ = _request(port, "POST", "/api/jobs/refresh",
                         body={"department": "cs", "limit": 1})
    assert status == 403


def test_refresh_with_bad_host_is_forbidden(server):
    _, _, port = server
    status, _ = _request(port, "POST", "/api/jobs/refresh",
                         body={"department": "cs", "limit": 1},
                         headers=CSRF, host="evil.example.com")
    assert status == 403


def test_refresh_with_foreign_origin_is_forbidden(server):
    _, _, port = server
    status, _ = _request(port, "POST", "/api/jobs/refresh",
                         body={"department": "cs", "limit": 1},
                         headers={**CSRF, "Origin": "http://evil.example.com"})
    assert status == 403


def test_get_endpoints_reject_bad_host(server):
    _, _, port = server
    status, _ = _request(port, "GET", "/api/health", host="evil.example.com")
    assert status == 403


# ── refresh lifecycle ─────────────────────────────────────────────────────────

def test_refresh_starts_job_and_completes(server):
    _, _, port = server
    status, body = _request(port, "POST", "/api/jobs/refresh",
                            body={"department": "cs", "limit": 1, "web": False},
                            headers=CSRF)
    assert status == 201
    job_id = body["job_id"]
    done = _wait_status(port, job_id, "done")
    assert done is not None
    assert "job-ran" in done["log_tail"]

    status, listing = _request(port, "GET", "/api/jobs")
    assert status == 200
    assert any(j["id"] == job_id for j in listing["jobs"])


def test_unknown_department_is_rejected(server):
    _, _, port = server
    status, body = _request(port, "POST", "/api/jobs/refresh",
                            body={"department": "biology", "limit": 1},
                            headers=CSRF)
    assert status == 400


def test_get_unknown_job_is_404(server):
    _, _, port = server
    status, _ = _request(port, "GET", "/api/jobs/99999")
    assert status == 404


def test_second_refresh_while_running_is_409(server, monkeypatch):
    srv_mod, jobs, port = server
    jobs._build_cmd = _slow_builder
    status, body = _request(port, "POST", "/api/jobs/refresh",
                            body={"department": "cs", "limit": 80, "web": True},
                            headers=CSRF)
    assert status == 201
    job_id = body["job_id"]
    try:
        status2, _ = _request(port, "POST", "/api/jobs/refresh",
                              body={"department": "cs", "limit": 80, "web": True},
                              headers=CSRF)
        assert status2 == 409
    finally:
        _request(port, "POST", f"/api/jobs/{job_id}/cancel", headers=CSRF)


def test_refresh_all_starts_a_refresh_all_job(server):
    _, _, port = server
    status, body = _request(port, "POST", "/api/jobs/refresh",
                            body={"scope": "all", "web": False}, headers=CSRF)
    assert status == 201
    job_id = body["job_id"]
    done = _wait_status(port, job_id, "done")
    assert done is not None
    assert done["type"] == "refresh_all"


def test_health_reports_departments_from_registry(server):
    _, _, port = server
    status, body = _request(port, "GET", "/api/health")
    assert status == 200
    sup = {d["key"] for d in body["departments"]["supported"]}
    unsup = {d["key"] for d in body["departments"]["unsupported"]}
    assert "cs" in sup
    assert "informatics" not in sup        # static but unverified → not refreshed yet
    assert "ds" in unsup and "informatics" in unsup


def test_health_last_refresh_all_is_null_initially(server):
    _, _, port = server
    _, body = _request(port, "GET", "/api/health")
    assert body["last_refresh_all"] is None


def test_health_last_refresh_all_after_a_completed_run(server, tmp_path):
    srv_mod, jobs, port = server
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO jobs(type,args,status,started_at,finished_at) "
        "VALUES('refresh_all','{\"scope\":\"all\",\"web\":false}','done',"
        "'2026-06-13 10:00:00','2026-06-13 10:15:00')")
    conn.commit()
    conn.close()
    _, body = _request(port, "GET", "/api/health")
    assert body["last_refresh_all"]["duration_seconds"] == 900


def test_cancel_running_job(server):
    srv_mod, jobs, port = server
    jobs._build_cmd = _slow_builder
    status, body = _request(port, "POST", "/api/jobs/refresh",
                            body={"department": "cs", "limit": 80, "web": True},
                            headers=CSRF)
    assert status == 201
    job_id = body["job_id"]
    assert _wait_status(port, job_id, "running") is not None
    cstatus, cbody = _request(port, "POST", f"/api/jobs/{job_id}/cancel", headers=CSRF)
    assert cstatus == 200
    assert _wait_status(port, job_id, "cancelled") is not None

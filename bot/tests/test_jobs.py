"""Tests for the dashboard control-plane JobManager (bot/services/jobs.py).

The JobManager is the framework-agnostic core: it owns the `jobs` table, the
one-at-a-time lock, subprocess spawn/cancel, startup reconciliation, and log
tailing. It is decoupled from the HTTP layer so it can be unit-tested without
Ollama or the network — tests inject a fake command builder that spawns a
trivial python subprocess instead of the real ingest pipeline.
"""

import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bot.services.jobs import (
    JobBusyError,
    JobManager,
    JobNotFoundError,
    build_refresh_command,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_manager(tmp_path: Path, build_cmd=None) -> JobManager:
    mgr = JobManager(
        db_path=str(tmp_path / "test.db"),
        repo_root=str(tmp_path),
        python_bin=sys.executable,
        jobs_log_dir=str(tmp_path / "logs" / "jobs"),
        build_cmd=build_cmd,
    )
    mgr.ensure_schema()
    return mgr


# A builder that ignores args and runs a trivial, fast, successful process.
def _fast_ok_builder(prints="done"):
    def build(job_type, args):
        return [sys.executable, "-c", f"print({prints!r})"]
    return build


# A builder that sleeps so the job stays 'running' (for lock / cancel tests).
def _slow_builder(seconds=30):
    def build(job_type, args):
        return [sys.executable, "-c", f"import time; time.sleep({seconds})"]
    return build


# ── schema ──────────────────────────────────────────────────────────────────

def test_ensure_schema_creates_jobs_table(tmp_path):
    mgr = _make_manager(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    assert {"id", "type", "args", "status", "started_at",
            "finished_at", "log_path", "pid", "summary"} <= cols


def test_ensure_schema_is_idempotent(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.ensure_schema()  # second call must not raise


# ── command builder ───────────────────────────────────────────────────────────

def test_build_refresh_command_includes_web_when_requested():
    cmd = build_refresh_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db",
        department="cs", limit=80, web=True)
    assert "--web" in cmd
    assert cmd[:2] == ["/venv/python", "/repo/scripts/ingest_faculty.py"]
    assert "--overview" in cmd and "--commit" in cmd
    assert "--department" in cmd and "cs" in cmd
    assert "--limit" in cmd and "80" in cmd
    assert "--db" in cmd and "/repo/g.db" in cmd


def test_build_refresh_command_omits_web_when_disabled():
    cmd = build_refresh_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db",
        department="cs", limit=5, web=False)
    assert "--web" not in cmd
    assert "--overview" in cmd and "--commit" in cmd  # always on


# ── lifecycle ─────────────────────────────────────────────────────────────────

def test_start_refresh_runs_to_done(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("hello-from-job"))
    job = mgr.start_refresh(department="cs", limit=5, web=False)
    assert job["job_id"] >= 1
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    row = mgr.get_job(job["job_id"])
    assert row["status"] == "done"
    assert row["finished_at"]
    assert "hello-from-job" in row["log_tail"]


def test_failed_process_marks_job_failed(tmp_path):
    def build(job_type, args):
        return [sys.executable, "-c", "import sys; sys.exit(3)"]
    mgr = _make_manager(tmp_path, build_cmd=build)
    job = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "failed")


def test_log_tail_is_written_to_per_job_file(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("marker-line"))
    job = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    log_path = Path(mgr.get_job(job["job_id"])["log_path"])
    assert log_path.exists()
    assert "marker-line" in log_path.read_text()


# ── one-job lock ──────────────────────────────────────────────────────────────

def test_second_job_while_running_raises_busy(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_slow_builder(30))
    first = mgr.start_refresh(department="cs", limit=80, web=True)
    try:
        with pytest.raises(JobBusyError):
            mgr.start_refresh(department="cs", limit=80, web=True)
    finally:
        mgr.cancel(first["job_id"])


def test_can_start_again_after_previous_finishes(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder())
    first = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(first["job_id"])["status"] == "done")
    second = mgr.start_refresh(department="cs", limit=5, web=False)  # must not raise
    assert second["job_id"] != first["job_id"]


# ── cancel ────────────────────────────────────────────────────────────────────

def test_cancel_terminates_running_job(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_slow_builder(30))
    job = mgr.start_refresh(department="cs", limit=80, web=True)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "running")
    mgr.cancel(job["job_id"])
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "cancelled")


def test_cancel_unknown_job_raises(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder())
    with pytest.raises(JobNotFoundError):
        mgr.cancel(99999)


# ── listing & lookup ──────────────────────────────────────────────────────────

def test_list_jobs_returns_recent_first(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder())
    a = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(a["job_id"])["status"] == "done")
    b = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(b["job_id"])["status"] == "done")
    jobs = mgr.list_jobs(limit=10)
    assert [j["id"] for j in jobs[:2]] == [b["job_id"], a["job_id"]]


def test_get_unknown_job_returns_none(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder())
    assert mgr.get_job(99999) is None


# ── startup reconciliation ────────────────────────────────────────────────────

def test_reconcile_marks_orphaned_running_jobs_interrupted(tmp_path):
    mgr = _make_manager(tmp_path)
    # Simulate a job left 'running' by a previous process that died.
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO jobs(type,args,status,started_at) VALUES('refresh','{}','running','2026-06-13 00:00:00')")
    conn.commit()
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.close()

    mgr.reconcile_startup()

    row = mgr.get_job(job_id)
    assert row["status"] == "interrupted"
    assert row["finished_at"]


# ── log-tail safety ───────────────────────────────────────────────────────────

def test_get_job_does_not_read_arbitrary_paths(tmp_path):
    """log_path comes from the DB row keyed by integer id — never user input."""
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("safe"))
    job = mgr.start_refresh(department="cs", limit=5, web=False)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    # The id is an int; get_job only ever opens the stored log_path.
    row = mgr.get_job(job["job_id"])
    assert row["log_path"].endswith(f"{job['job_id']}.log")

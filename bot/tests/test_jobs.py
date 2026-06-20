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
    build_crawl_section_command,
    build_refresh_all_command,
    build_refresh_command,
    build_refresh_scholar_command,
    build_seed_roster_command,
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


def test_build_refresh_scholar_command_maps_all_args():
    cmd = build_refresh_scholar_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db",
        scope="ywcc", older_than=30, embed=True)
    assert cmd[:2] == ["/venv/python", "/repo/scripts/refresh_scholar.py"]
    assert "--commit" in cmd                       # always gated-commit (job runs for real)
    assert "--org" in cmd and "ywcc" in cmd
    assert "--older-than" in cmd and "30" in cmd
    assert "--embed" in cmd
    assert "--db" in cmd and "/repo/g.db" in cmd


def test_build_refresh_scholar_command_all_scope_omits_optionals():
    cmd = build_refresh_scholar_command(
        python_bin="p", repo_root="/r", db_path="/r/g.db",
        scope=None, older_than=None, embed=False)
    assert "--org" not in cmd
    assert "--older-than" not in cmd
    assert "--embed" not in cmd
    assert "--commit" in cmd


def test_default_build_cmd_routes_refresh_scholar(tmp_path):
    mgr = JobManager(db_path=str(tmp_path / "t.db"), repo_root="/repo",
                     python_bin="/venv/python")
    cmd = mgr._default_build_cmd("refresh_scholar", {"scope": "ywcc", "older_than": 30, "embed": True})
    assert "refresh_scholar.py" in cmd[1]
    assert "--org" in cmd and "ywcc" in cmd and "--embed" in cmd


def test_scholar_job_summary_uses_completion_line(tmp_path):
    line = "Scholar refresh complete: 5 updated, 3 areas, 0 failed of 5."
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder(line))
    job = mgr.start_refresh_scholar(scope="ywcc", older_than=30, embed=False)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    assert "Scholar refresh complete" in (mgr.get_job(job["job_id"])["summary"] or "")


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


# ── refresh-all (the "Refresh NJIT KB" button) ────────────────────────────────

def test_build_refresh_all_command_runs_every_department():
    cmd = build_refresh_all_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db", web=True)
    assert cmd[:2] == ["/venv/python", "/repo/scripts/ingest_faculty.py"]
    assert "--all" in cmd
    assert "--overview" in cmd and "--commit" in cmd
    assert "--web" in cmd
    assert "--department" not in cmd and "--limit" not in cmd


def test_build_refresh_all_command_omits_web_when_disabled():
    cmd = build_refresh_all_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db", web=False)
    assert "--web" not in cmd
    assert "--all" in cmd


def test_start_refresh_all_runs_to_done(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("all-ran"))
    job = mgr.start_refresh_all(web=False)
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    row = mgr.get_job(job["job_id"])
    assert row["type"] == "refresh_all"
    assert "all-ran" in row["log_tail"]


def test_build_crawl_section_command_invokes_refresh():
    cmd = build_crawl_section_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db", section="registrar")
    assert cmd[:2] == ["/venv/python", "/repo/scripts/crawl_njit_section.py"]
    assert "registrar" in cmd and "--refresh" in cmd


def test_build_crawl_section_command_defaults_to_all():
    cmd = build_crawl_section_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db", section="all")
    assert "all" in cmd and "--refresh" in cmd


def test_start_crawl_section_runs_to_done(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("crawl-ran"))
    job = mgr.start_crawl_section(section="bursar")
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    row = mgr.get_job(job["job_id"])
    assert row["type"] == "crawl_section"
    assert "crawl-ran" in row["log_tail"]


def test_build_seed_roster_command_maps_roster_to_script():
    cmd = build_seed_roster_command(
        python_bin="/venv/python", repo_root="/repo", db_path="/repo/g.db",
        roster="senior-administration")
    assert cmd[:2] == ["/venv/python", "/repo/scripts/ingest_njit_administration.py"]
    assert "--commit" in cmd and "/repo/g.db" in cmd


def test_start_seed_roster_runs_to_done(tmp_path):
    mgr = _make_manager(tmp_path, build_cmd=_fast_ok_builder("roster-ran"))
    job = mgr.start_seed_roster(roster="theatre")
    assert _wait_until(lambda: mgr.get_job(job["job_id"])["status"] == "done")
    row = mgr.get_job(job["job_id"])
    assert row["type"] == "seed_roster"
    assert "roster-ran" in row["log_tail"]


# ── duration ──────────────────────────────────────────────────────────────────

def _insert_job(tmp_path, *, type, status, started, finished, args="{}"):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO jobs(type,args,status,started_at,finished_at) VALUES(?,?,?,?,?)",
        (type, args, status, started, finished))
    conn.commit()
    jid = conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
    conn.close()
    return jid


def test_duration_seconds_computed_for_finished_job(tmp_path):
    mgr = _make_manager(tmp_path)
    jid = _insert_job(tmp_path, type="refresh_all", status="done",
                      started="2026-06-13 10:00:00", finished="2026-06-13 10:20:00")
    assert mgr.get_job(jid)["duration_seconds"] == 1200


def test_duration_seconds_none_while_running(tmp_path):
    mgr = _make_manager(tmp_path)
    jid = _insert_job(tmp_path, type="refresh_all", status="running",
                      started="2026-06-13 10:00:00", finished=None)
    assert mgr.get_job(jid)["duration_seconds"] is None


# ── estimate ──────────────────────────────────────────────────────────────────

def test_estimate_prefers_matching_web_setting(tmp_path):
    mgr = _make_manager(tmp_path)
    _insert_job(tmp_path, type="refresh_all", status="done",
                started="2026-06-13 10:00:00", finished="2026-06-13 10:10:00",
                args='{"scope":"all","web":false}')
    _insert_job(tmp_path, type="refresh_all", status="done",
                started="2026-06-13 11:00:00", finished="2026-06-13 12:00:00",
                args='{"scope":"all","web":true}')
    assert mgr.estimate_refresh_all(web=False)["duration_seconds"] == 600
    assert mgr.estimate_refresh_all(web=True)["duration_seconds"] == 3600


def test_estimate_falls_back_when_no_web_match(tmp_path):
    mgr = _make_manager(tmp_path)
    _insert_job(tmp_path, type="refresh_all", status="done",
                started="2026-06-13 11:00:00", finished="2026-06-13 12:00:00",
                args='{"scope":"all","web":true}')
    est = mgr.estimate_refresh_all(web=False)  # no web=false run → fall back
    assert est["duration_seconds"] == 3600


def test_estimate_none_when_no_prior_run(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.estimate_refresh_all(web=False) is None


def _insert_job_with_log(tmp_path, job_id, log_text, type="refresh_all"):
    log = tmp_path / "logs" / "jobs" / f"{job_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(log_text)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute(
        "INSERT INTO jobs(id,type,args,status,started_at,log_path) "
        "VALUES(?,?,'{}','running','2026-06-13 00:00:00',?)",
        (job_id, type, str(log)))
    conn.commit()
    conn.close()


def test_summarize_prefers_completion_line_and_strips_ansi(tmp_path):
    mgr = _make_manager(tmp_path)
    _insert_job_with_log(tmp_path, 99,
        "\x1b[0;32m✓ Alice: +0 new, ~1 updated, -0 removed, =3 unchanged\x1b[0m\n"
        "\x1b[0;32m✓ Bob: +0 new, ~1 updated, -0 removed, =3 unchanged\x1b[0m\n"
        "\nRefresh NJIT KB complete (ok) — Computer Science: 58 profiles\n")
    s = mgr._summarize(99)
    assert s == "Refresh NJIT KB complete (ok) — Computer Science: 58 profiles"
    assert "\x1b" not in s


def test_summarize_falls_back_to_change_line_stripped(tmp_path):
    mgr = _make_manager(tmp_path)
    _insert_job_with_log(tmp_path, 98,
        "\x1b[0;32m✓ Alice: +1 new, ~0 updated, -0 removed\x1b[0m\n", type="refresh")
    s = mgr._summarize(98)
    assert "Alice: +1 new" in s
    assert "\x1b" not in s


def test_estimate_excludes_non_done_runs(tmp_path):
    mgr = _make_manager(tmp_path)
    _insert_job(tmp_path, type="refresh_all", status="failed",
                started="2026-06-13 10:00:00", finished="2026-06-13 10:05:00",
                args='{"scope":"all","web":false}')
    _insert_job(tmp_path, type="refresh_all", status="interrupted",
                started="2026-06-13 11:00:00", finished="2026-06-13 11:01:00",
                args='{"scope":"all","web":false}')
    assert mgr.estimate_refresh_all(web=False) is None

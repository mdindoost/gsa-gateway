"""JobManager — the dashboard control-plane's job runner core.

Framework-agnostic: owns the ``jobs`` table, a one-at-a-time lock, subprocess
spawn/cancel, startup reconciliation, and bounded log tailing. The HTTP layer
(``v2/local_server.py``) is a thin wrapper over this. Decoupled so it can be
unit-tested without Ollama or the network — callers can inject a ``build_cmd``.

Design notes (see docs/superpowers/specs/2026-06-12-dashboard-control-plane.md):
- jobs are spawned with ``start_new_session=True`` (own process group) so cancel
  can ``killpg`` the python child and its workers together.
- the ``jobs`` table is created here with ``CREATE TABLE IF NOT EXISTS`` — the bot
  never runs the v2 schema's ``create_all``, so the backend owns the table it writes.
- on startup any ``running`` row is reconciled to ``interrupted`` (its in-memory
  watcher died with the previous process; we no longer track the detached child).
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("jobs")

TAIL_BYTES = 16 * 1024
_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # color codes in the ingest output


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


class JobBusyError(Exception):
    """Raised when a job is already running (maps to HTTP 409)."""


class JobNotFoundError(Exception):
    """Raised when an operation targets a job id that does not exist (HTTP 404)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_refresh_command(*, python_bin, repo_root, db_path, department, limit,
                          web, changes_log=None) -> list[str]:
    """Build the faculty-refresh command (calls ingest_faculty.py directly).

    ``--overview --commit`` are always present (overviews + persisted, backed-up
    writes). ``--web`` is honored from the caller so the dashboard toggle is real.
    """
    script = str(Path(repo_root) / "scripts" / "ingest_faculty.py")
    cmd = [
        python_bin, script,
        "--department", str(department),
        "--limit", str(limit),
        "--overview", "--commit",
        "--db", str(db_path),
    ]
    if changes_log:
        cmd += ["--changes-log", str(changes_log)]
    if web:
        cmd.append("--web")
    return cmd


def build_refresh_all_command(*, python_bin, repo_root, db_path, web,
                              changes_log=None) -> list[str]:
    """Build the all-departments refresh (the 'Refresh NJIT KB' button).

    Runs every statically-discoverable department in one process (one backup).
    No --department/--limit — it always crawls the full list per department.
    """
    script = str(Path(repo_root) / "scripts" / "ingest_faculty.py")
    cmd = [python_bin, script, "--all", "--overview", "--commit", "--db", str(db_path)]
    if changes_log:
        cmd += ["--changes-log", str(changes_log)]
    if web:
        cmd.append("--web")
    return cmd


def build_explore_command(*, python_bin, repo_root, db_path, depth, frontier, reset) -> list[str]:
    """Build the KG gather command (run_explore.py). ``frontier`` processes pending personal
    sites instead of a hub crawl; ``reset`` re-derives the graph + crawler KB from scratch."""
    script = str(Path(repo_root) / "scripts" / "run_explore.py")
    cmd = [python_bin, script, "--db", str(db_path), "--depth", str(depth)]
    if frontier:
        cmd.append("--frontier")
    if reset:
        cmd.append("--reset")
    return cmd


def build_crawl_section_command(*, python_bin, repo_root, db_path, section) -> list[str]:
    """Build the NJIT office-refresh command (crawl_njit_section.py --refresh): fetch the
    office's grad-relevant pages, ingest them live (gated backup), capture staff, embed.
    ``section`` is a SECTIONS key (e.g. 'registrar') or 'all'."""
    script = str(Path(repo_root) / "scripts" / "crawl_njit_section.py")
    return [python_bin, script, str(section), "--refresh"]


def _duration_seconds(started_at, finished_at):
    """Wall-clock seconds between two UTC 'YYYY-MM-DD HH:MM:SS' strings, or None."""
    if not started_at or not finished_at:
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        start = datetime.strptime(started_at, fmt)
        finish = datetime.strptime(finished_at, fmt)
    except (TypeError, ValueError):
        return None
    return int((finish - start).total_seconds())


class JobManager:
    def __init__(self, db_path, repo_root, python_bin, jobs_log_dir=None,
                 build_cmd=None, changes_log=None):
        self.db_path = str(db_path)
        self.repo_root = str(repo_root)
        self.python_bin = python_bin
        self.jobs_log_dir = (Path(jobs_log_dir) if jobs_log_dir
                             else Path(repo_root) / "logs" / "jobs")
        self.changes_log = changes_log
        self._build_cmd = build_cmd or self._default_build_cmd
        self._lock = threading.Lock()
        self._current_proc: subprocess.Popen | None = None
        self._current_id: int | None = None

    # ── command ──────────────────────────────────────────────────────────────
    def _default_build_cmd(self, job_type, args) -> list[str]:
        if job_type == "explore":
            return build_explore_command(
                python_bin=self.python_bin, repo_root=self.repo_root,
                db_path=self.db_path, depth=args.get("depth", 3),
                frontier=args.get("frontier", False), reset=args.get("reset", False))
        if job_type == "refresh_all":
            return build_refresh_all_command(
                python_bin=self.python_bin, repo_root=self.repo_root,
                db_path=self.db_path, web=args.get("web", False),
                changes_log=self.changes_log)
        if job_type == "crawl_section":
            return build_crawl_section_command(
                python_bin=self.python_bin, repo_root=self.repo_root,
                db_path=self.db_path, section=args.get("section", "all"))
        return build_refresh_command(
            python_bin=self.python_bin, repo_root=self.repo_root,
            db_path=self.db_path, department=args.get("department", "cs"),
            limit=args.get("limit", 80), web=args.get("web", True),
            changes_log=self.changes_log)

    # ── db ───────────────────────────────────────────────────────────────────
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    args TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    log_path TEXT,
                    pid INTEGER,
                    summary TEXT)""")
            conn.commit()
        finally:
            conn.close()
        self.jobs_log_dir.mkdir(parents=True, exist_ok=True)

    def reconcile_startup(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE jobs SET status='interrupted', finished_at=? "
                "WHERE status='running'", (_utc_now(),))
            conn.commit()
        finally:
            conn.close()

    def _update(self, job_id, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [*fields.values(), int(job_id)]
        conn = self._conn()
        try:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()

    # ── public API ─────────────────────────────────────────────────────────--
    def start_refresh(self, department="cs", limit=80, web=True) -> dict:
        return self._start("refresh",
                           {"department": department, "limit": limit, "web": web})

    def start_refresh_all(self, web=False) -> dict:
        return self._start("refresh_all", {"scope": "all", "web": web})

    def start_explore(self, depth=3, frontier=False, reset=False) -> dict:
        return self._start("explore",
                           {"depth": depth, "frontier": frontier, "reset": reset})

    def start_crawl_section(self, section="all") -> dict:
        """Refresh one NJIT office (or 'all') from njit.edu: fetch + ingest live + embed."""
        return self._start("crawl_section", {"section": section})

    def estimate_refresh_all(self, web=False) -> dict | None:
        """Duration estimate for an all-departments run, from the last completed one.

        Prefers the most recent `done` refresh_all whose `web` matches; else the
        most recent `done` refresh_all regardless; None if there is no prior run.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT args,started_at,finished_at FROM jobs "
                "WHERE type='refresh_all' AND status='done' ORDER BY id DESC").fetchall()
        finally:
            conn.close()
        match = fallback = None
        for r in rows:
            dur = _duration_seconds(r["started_at"], r["finished_at"])
            if dur is None:
                continue
            try:
                row_web = bool(json.loads(r["args"] or "{}").get("web"))
            except (TypeError, ValueError):
                row_web = False
            cand = {"duration_seconds": dur, "web": row_web,
                    "finished_at": r["finished_at"]}
            if fallback is None:
                fallback = cand
            if row_web == bool(web):
                match = cand
                break
        return match or fallback

    def _start(self, job_type, args) -> dict:
        with self._lock:
            if self._current_proc is not None and self._current_proc.poll() is None:
                raise JobBusyError("a job is already running")

            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO jobs(type,args,status,started_at) "
                    "VALUES(?,?,'running',?)",
                    (job_type, json.dumps(args), _utc_now()))
                job_id = cur.lastrowid
                conn.commit()
            finally:
                conn.close()

            self.jobs_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.jobs_log_dir / f"{job_id}.log"
            cmd = self._build_cmd(job_type, args)
            logf = open(log_path, "wb")
            try:
                proc = subprocess.Popen(
                    cmd, cwd=self.repo_root, stdout=logf,
                    stderr=subprocess.STDOUT, start_new_session=True,
                    # unbuffered child stdout so the dashboard log tail streams
                    # live instead of appearing to pause between buffer flushes.
                    env={**os.environ, "PYTHONUNBUFFERED": "1"})
            except Exception:
                logf.close()
                self._update(job_id, status="failed", finished_at=_utc_now(),
                             summary="failed to spawn")
                raise

            self._update(job_id, log_path=str(log_path), pid=proc.pid)
            self._current_proc = proc
            self._current_id = job_id
            threading.Thread(target=self._watch, args=(job_id, proc, logf),
                             daemon=True).start()
            logger.info("job %d started: %s", job_id, " ".join(cmd))
            return {"job_id": job_id, "status": "running", "log_path": str(log_path)}

    def _watch(self, job_id, proc, logf) -> None:
        try:
            rc = proc.wait()
        finally:
            try:
                logf.close()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            if self._current_id == job_id:
                self._current_proc = None
                self._current_id = None
        # A cancel() may have already set 'cancelled' — don't clobber it.
        existing = self.get_job(job_id)
        if existing and existing["status"] == "cancelled":
            self._update(job_id, finished_at=_utc_now())
            return
        status = "done" if rc == 0 else "failed"
        self._update(job_id, status=status, finished_at=_utc_now(),
                     summary=self._summarize(job_id))
        logger.info("job %d finished: %s (rc=%s)", job_id, status, rc)

    def cancel(self, job_id) -> dict:
        job_id = int(job_id)
        row = self.get_job(job_id)
        if row is None:
            raise JobNotFoundError(f"job {job_id} not found")
        if row["status"] != "running":
            return {"job_id": job_id, "status": row["status"]}
        # Mark cancelled BEFORE killing so the watcher won't overwrite to failed.
        self._update(job_id, status="cancelled")
        with self._lock:
            proc = self._current_proc if self._current_id == job_id else None
        pid = proc.pid if (proc is not None and proc.poll() is None) else row.get("pid")
        if pid:
            self._term_group(pid)
        return {"job_id": job_id, "status": "cancelled"}

    @staticmethod
    def _term_group(pid) -> None:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def list_jobs(self, limit=20) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id,type,args,status,started_at,finished_at,summary "
                "FROM jobs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["duration_seconds"] = _duration_seconds(d.get("started_at"),
                                                      d.get("finished_at"))
            out.append(d)
        return out

    def get_job(self, job_id) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?",
                               (int(job_id),)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        d = dict(row)
        d["log_tail"] = self._tail(d.get("log_path"))
        d["duration_seconds"] = _duration_seconds(d.get("started_at"),
                                                  d.get("finished_at"))
        return d

    # ── logs ─────────────────────────────────────────────────────────────────
    def _tail(self, log_path, max_bytes=TAIL_BYTES) -> str:
        if not log_path:
            return ""
        p = Path(log_path)
        if not p.exists():
            return ""
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
        return data.decode("utf-8", "replace")

    def _summarize(self, job_id) -> str:
        conn = self._conn()
        try:
            r = conn.execute("SELECT log_path FROM jobs WHERE id=?",
                             (int(job_id),)).fetchone()
        finally:
            conn.close()
        tail = self._tail(r["log_path"] if r else None)
        lines = [_strip_ansi(ln).strip() for ln in tail.splitlines() if ln.strip()]
        # Prefer the explicit completion line the --all run prints.
        for ln in reversed(lines):
            if "refresh njit kb complete" in ln.lower():
                return ln[:300]
        # Otherwise the most recent change-count line (single-department run).
        for ln in reversed(lines):
            low = ln.lower()
            if ("new" in low and "updated" in low) or "changed" in low:
                return ln[:300]
        return lines[-1][:300] if lines else ""

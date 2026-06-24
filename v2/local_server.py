"""GSA Gateway v2 — local admin server (safe dashboard write path).

A tiny stdlib HTTP server on 127.0.0.1:5555. The dashboard (in a laptop browser)
reaches it over an SSH tunnel and reads/writes the live gsa_gateway.db directly —
no file download/upload, no manual SQL copy-paste. Writes are applied immediately;
the v2 scheduler (in the bot) picks up new posts within ~30s.

Security: binds to 127.0.0.1 ONLY (never 0.0.0.0). Only reachable through the SSH
tunnel; SSH provides the authentication.

Stdlib only — http.server + json + sqlite3.

Run:  python v2/local_server.py
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# The live db lives at the repo root (this file is v2/local_server.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so `bot.services.jobs` imports when run directly
DB_PATH = REPO_ROOT / "gsa_gateway.db"
DASHBOARD_DIR = REPO_ROOT / "dashboard"          # served so one URL = whole app
HOST = "127.0.0.1"   # localhost ONLY — reachable only via SSH tunnel
PORT = int(os.environ.get("GSA_SERVER_PORT", "5555"))  # override for testing/alt ports
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")

from bot.services.jobs import JobBusyError, JobManager, JobNotFoundError  # noqa: E402
from v2.core.ingestion.departments import DEPARTMENTS as DEPT_REGISTRY  # noqa: E402
from v2.core.ingestion.departments import supported as supported_depts  # noqa: E402
from v2.core.ingestion.entry_points import crawl_scope as _crawl_scope  # noqa: E402
from v2.core.ingestion import scholar as _scholar  # noqa: E402

# Control-plane job runner (faculty refresh, …). Same DB the bot/dashboard use.
JOBS = JobManager(db_path=DB_PATH, repo_root=REPO_ROOT, python_bin=sys.executable)
# ThreadingHTTPServer runs requests concurrently; this serializes the destructive restore so two
# (e.g. a double-click) can't copy over the live DB at once.
_RESTORE_LOCK = threading.Lock()

# Host-header allowlist (defeats DNS-rebinding) — only these Hosts are served.
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}", "localhost", "127.0.0.1"}
# Origins a browser may legitimately use to POST control calls (file:// == "null").
ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}", "null"}

# Static dashboard files served from the same origin (no CORS, one tunnel).
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
          "/style.css": "style.css", "/posts_logic.js": "posts_logic.js"}
CONTENT_TYPES = {".html": "text/html", ".js": "application/javascript",
                 ".css": "text/css"}

logger = logging.getLogger("local_server")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_FMT = "%Y-%m-%d %H:%M:%S"


def _clamp_delete_at(delete_at: str, baseline: str) -> str:
    """Clamp a post's delete_at to at most baseline+48h (Telegram's bot-delete ceiling). The
    baseline is the post's send time (scheduled_for, or now for asap). Unparseable input is
    returned unchanged (defensive)."""
    try:
        d = datetime.strptime(delete_at, _FMT)
        cap = datetime.strptime(baseline, _FMT) + timedelta(hours=48)
    except (ValueError, TypeError):
        return delete_at
    return min(d, cap).strftime(_FMT)


def _ollama_up() -> bool:
    # Overviews + embeddings need Ollama; surface its status so the UI can warn.
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class GatewayHandler(BaseHTTPRequestHandler):
    # ── db ───────────────────────────────────────────────────────────────────
    def _conn(self):
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ── responses ──────────────────────────────────────────────────────────--
    def _cors(self):
        # localhost-only server behind an SSH tunnel; * is safe and works from
        # both file:// (origin "null") and http://localhost dashboards.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=400):
        self._json({"success": False, "error": msg}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        if length > 10 * 1024 * 1024:  # M-new-3: 10 MB cap
            raise ValueError("Request body too large (max 10 MB)")
        return json.loads(self.rfile.read(length).decode() or "{}")

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    # ── security guards ────────────────────────────────────────────────────---
    def _host_ok(self) -> bool:
        # Defeats DNS-rebinding: a rebound hostname yields a Host we don't allow.
        return (self.headers.get("Host") or "") in ALLOWED_HOSTS

    def _csrf_ok(self) -> bool:
        # State-changing /api/* calls require our custom header (a cross-site page
        # can't set it without a CORS preflight we never grant) + an allowed Origin.
        if self.headers.get("X-GSA-Dashboard") != "1":
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ────────────────────────────────────────────────────────────────---
    def do_GET(self):
        if not self._host_ok():
            return self._error("forbidden host", 403)
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path in STATIC:
                return self._send_static(STATIC[path])
            if path == "/api/health":
                return self._api_health()
            if path == "/api/jobs":
                return self._json({"jobs": JOBS.list_jobs(20)})
            if path == "/api/jobs/scholar-scopes":
                return self._api_scholar_scopes()
            if path == "/api/jobs/discover-scopes":
                return self._api_scholar_scopes(mode="discover")
            if path == "/api/backups":
                return self._api_list_backups()
            if path.startswith("/api/jobs/"):
                return self._api_get_job(path[len("/api/jobs/"):])
            if path == "/health":
                return self._json({
                    "status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists(),
                    "timestamp": utc_now(),
                })
            if path == "/db":  # consistent snapshot, for the dashboard's read layer
                return self._send_db_snapshot()
            conn = self._conn()
            try:
                if path == "/posts":
                    return self._json(self._get_posts(conn, qs))
                if path == "/orgs":
                    return self._json(self._get_orgs(conn))
                if path == "/knowledge":
                    return self._json(self._get_knowledge(conn, qs))
                if path == "/settings":
                    return self._json(self._get_settings(conn, qs))
                if path == "/analytics":
                    return self._json(self._get_analytics(conn, qs))
            finally:
                conn.close()
            # ── judging endpoints (live API, not sql.js) ──────────────────
            if path == "/judging/events":
                from v2.core.judging import db as jdb
                conn = self._conn()
                try:
                    return self._json(jdb.list_events(conn))
                finally:
                    conn.close()
            if path.startswith("/judging/events/"):
                return self._judging_get(path)
            self._error("Not found", 404)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GET %s failed", path)
            self._error(str(exc), 500)

    # ── POST ───────────────────────────────────────────────────────────────---
    def do_POST(self):
        if not self._host_ok():
            return self._error("forbidden host", 403)
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            if not self._csrf_ok():
                return self._error("forbidden", 403)
            try:
                if path == "/api/jobs/refresh":
                    return self._api_refresh()
                if path == "/api/jobs/explore":
                    return self._api_explore()
                if path == "/api/jobs/refresh-scholar":
                    return self._api_refresh_scholar()
                if path == "/api/jobs/discover-scholar":
                    return self._api_discover_scholar()
                if path == "/api/jobs/crawl-section":
                    return self._api_crawl_section()
                if path == "/api/jobs/seed-roster":
                    return self._api_seed_roster()
                if path == "/api/backups/restore":
                    return self._api_restore_backup()
                if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                    return self._api_cancel(path[len("/api/jobs/"):-len("/cancel")])
                return self._error("Not found", 404)
            except Exception as exc:  # noqa: BLE001
                logger.exception("POST %s failed", path)
                return self._error(str(exc), 500)
        try:
            # C-new-1: CSRF guard for all non-/api/* writes
            if not self._csrf_ok():
                return self._error("forbidden", 403)
            body = self._body()
            conn = self._conn()
            try:
                if path == "/posts":
                    return self._json(self._post_post(conn, body))
                if path == "/knowledge":
                    return self._json(self._post_knowledge(conn, body))
                if path == "/orgs":
                    return self._json(self._post_org(conn, body))
                if path == "/settings":
                    return self._json(self._post_setting(conn, body))
                if path == "/people":
                    return self._json(self._post_person(conn, body))
                if path == "/people/remove":
                    return self._json(self._post_person_remove(conn, body))
            finally:
                conn.close()
            # ── judging write endpoints ───────────────────────────────────
            if path == "/judging/events":
                return self._judging_post_events(body)
            if path.startswith("/judging/events/"):
                return self._judging_post_event(path, body)
            self._error("Not found", 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s failed", path)
            self._error(str(exc), 500)

    # ── judging helpers ────────────────────────────────────────────────────────

    def _judging_get(self, path: str):
        """Handle GET /judging/events/<id>/<action>[/<sub>/<action2>]"""
        from v2.core.judging import db as jdb
        from v2.core.judging.calculator import export_csv, get_event_progress, get_leaderboard

        parts = path.strip("/").split("/")
        # parts: ['judging', 'events', '<id>', '<action>', ...]
        try:
            event_id = int(parts[2])
        except (IndexError, ValueError):
            return self._error("invalid event id", 400)
        action = parts[3] if len(parts) > 3 else None

        conn = self._conn()
        try:
            if action == "status":
                return self._json({
                    "event": jdb.get_event(conn, event_id),
                    "progress": get_event_progress(conn, event_id),
                    "judges": jdb.list_judges(conn, event_id),
                })
            if action == "results":
                event = jdb.get_event(conn, event_id)
                min_cov = event["min_coverage"] if event else None
                return self._json({
                    "event": event,
                    "leaderboard": get_leaderboard(conn, event_id, min_coverage=min_cov),
                })
            if action == "export":
                csv_text = export_csv(conn, event_id)
                body = csv_text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="judging_{event_id}.csv"',
                )
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            if action == "judges":
                return self._json(jdb.list_judges(conn, event_id))
            if action == "audience-results":
                return self._json({
                    "event": jdb.get_event(conn, event_id),
                    "results": jdb.get_audience_results(conn, event_id),
                })
            if action == "audit":
                # Full score-change history for the event, newest first.
                return self._json({"audit": jdb.get_score_audit(conn, event_id)})
            if action == "presenters":
                # /judging/events/<id>/presenters/<number>/scores — admin drill-down
                if len(parts) >= 6 and parts[5] == "scores":
                    try:
                        presenter_number = int(parts[4])
                    except (IndexError, ValueError):
                        return self._error("invalid presenter number", 400)
                    return self._json(
                        jdb.get_presenter_scores_detail(conn, event_id, presenter_number)
                    )
                return self._json(jdb.list_presenters(conn, event_id))
        finally:
            conn.close()
        self._error("Not found", 404)

    def _judging_post_events(self, body: dict):
        """Handle POST /judging/events — create a new event."""
        from v2.core.judging import db as jdb

        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        criteria = body.get("criteria") or None
        top_n = int(body.get("top_n", 3))
        score_min = int(body.get("score_min", 1))
        score_max = int(body.get("score_max", 5))
        min_coverage = int(body.get("min_coverage", 3))
        audience_top_n = int(body.get("audience_top_n", 1))
        conn = self._conn()
        try:
            event_id = jdb.create_event(
                conn, name, criteria, top_n,
                score_min=score_min, score_max=score_max,
                min_coverage=min_coverage, audience_top_n=audience_top_n,
            )
            conn.commit()
            return self._json({
                "id": event_id, "name": name, "top_n": top_n,
                "score_min": score_min, "score_max": score_max,
                "min_coverage": min_coverage, "audience_top_n": audience_top_n,
            })
        finally:
            conn.close()

    def _judging_post_event(self, path: str, body: dict):
        """Handle POST /judging/events/<id>/<action>"""
        from v2.core.judging import db as jdb

        parts = path.strip("/").split("/")
        try:
            event_id = int(parts[2])
        except (IndexError, ValueError):
            return self._error("invalid event id", 400)
        action = parts[3] if len(parts) > 3 else None

        conn = self._conn()
        try:
            if action == "open":
                try:
                    jdb.set_event_status(conn, event_id, "open")
                    conn.commit()
                except sqlite3.IntegrityError:
                    # L-new-1: partial unique index blocks two simultaneous open events
                    raise ValueError("Another event is already open. Close it first.")
                return self._json({"success": True, "status": "open"})
            if action == "close":
                jdb.set_event_status(conn, event_id, "closed")
                conn.commit()
                return self._json({"success": True, "status": "closed"})
            if action == "delete-event":
                # Hard-delete the event + ALL its data (cascade). Irreversible.
                deleted = jdb.delete_event(conn, event_id)
                conn.commit()
                return self._json({"deleted": deleted})
            if action == "judges":
                name = (body.get("name") or "").strip()
                pin = (body.get("pin") or "").strip()
                if not name or not pin:
                    raise ValueError("name and pin required")
                if len(pin) < 6:
                    raise ValueError("PIN must be at least 6 characters")
                judge_id = jdb.add_judge(conn, event_id, name, pin)
                conn.commit()
                # C1: never echo the PIN back after creation — admin already knows it
                return self._json({"id": judge_id, "name": name, "has_pin": True})
            if action == "judges-delete":
                judge_id = int(body.get("judge_id", 0))
                # H4: give a clear 400 if judge has scores (FK prevents silent delete)
                score_count = conn.execute(
                    "SELECT COUNT(*) FROM judging_scores WHERE judge_id=?", (judge_id,)
                ).fetchone()[0]
                if score_count > 0:
                    raise ValueError(
                        f"Judge has {score_count} submitted score(s). "
                        "Delete their scores first via the Scores panel."
                    )
                jdb.delete_judge(conn, judge_id)
                conn.commit()
                return self._json({"success": True})
            if action == "presenters":
                csv_text = body.get("csv", "")
                count = jdb.load_presenters_csv(conn, event_id, csv_text)
                conn.commit()
                return self._json({"loaded": count})
            if action == "scores-delete":
                judge_id = int(body.get("judge_id", 0))
                presenter_number = int(body.get("presenter_number", 0))
                deleted = jdb.delete_score(conn, event_id, judge_id, presenter_number)
                if deleted:
                    # Audit the delete in the same transaction (no scores_json → NULL).
                    jdb.log_score_audit(
                        conn, event_id, judge_id, presenter_number,
                        action="admin_delete", actor="admin", actor_label="admin")
                conn.commit()
                return self._json({"deleted": deleted})
            if action == "scores-set":
                # Admin proxy entry (device-less judge) AND correction (overwrite an existing
                # score). Deliberately NOT gated on event status — corrections often happen
                # after the event closes. Upsert + audit in one transaction.
                judge_id = int(body.get("judge_id", 0))
                presenter_number = int(body.get("presenter_number", 0))
                raw_scores = body.get("scores") or []
                if not judge_id or not presenter_number:
                    raise ValueError("judge_id and presenter_number required")
                ev = jdb.get_event(conn, event_id)
                if ev is None:
                    raise ValueError("event not found")
                if jdb.get_presenter(conn, event_id, presenter_number) is None:
                    raise ValueError(f"Participant #{presenter_number} not found")
                try:
                    scores = [int(s) for s in raw_scores]
                except (TypeError, ValueError):
                    raise ValueError("scores must be a list of integers")
                # upsert_score validates length + range (raises ValueError → 400)
                existed, scores_json, final = jdb.upsert_score(
                    conn, event_id, judge_id, presenter_number, ev["criteria"], scores)
                jdb.log_score_audit(
                    conn, event_id, judge_id, presenter_number,
                    action="admin_edit" if existed else "admin_enter",
                    actor="admin", actor_label="admin",
                    scores_json=scores_json, final_score=final)
                conn.commit()
                return self._json({"success": True, "edited": existed, "final_score": final})
            if action == "present":
                presenter_number = int(body.get("presenter_number", 0))
                jdb.mark_presenter_present(conn, event_id, presenter_number)
                conn.commit()
                return self._json({"success": True})
            if action == "audience-open":
                jdb.set_audience_voting(conn, event_id, "open")
                conn.commit()
                return self._json({"success": True, "audience_voting": "open"})
            if action == "audience-close":
                jdb.set_audience_voting(conn, event_id, "closed")
                conn.commit()
                return self._json({"success": True, "audience_voting": "closed"})
            if action == "update":
                name = body.get("name")
                criteria = body.get("criteria")
                top_n = body.get("top_n")
                score_min = body.get("score_min")
                score_max = body.get("score_max")
                min_coverage = body.get("min_coverage")
                audience_top_n = body.get("audience_top_n")
                jdb.update_event(
                    conn, event_id,
                    name=name,
                    criteria=criteria,
                    top_n=int(top_n) if top_n is not None else None,
                    score_min=int(score_min) if score_min is not None else None,
                    score_max=int(score_max) if score_max is not None else None,
                    min_coverage=int(min_coverage) if min_coverage is not None else None,
                    audience_top_n=int(audience_top_n) if audience_top_n is not None else None,
                )
                conn.commit()
                return self._json({"success": True})
        finally:
            conn.close()
        self._error("Not found", 404)

    # ── DELETE ─────────────────────────────────────────────────────────────---
    def do_DELETE(self):
        if not self._host_ok():
            return self._error("forbidden host", 403)
        if not self._csrf_ok():  # M-new-1
            return self._error("forbidden", 403)
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 2 and parts[0] == "posts":
                conn = self._conn()
                try:
                    conn.execute("UPDATE posts SET status='cancelled' WHERE id=?", (int(parts[1]),))
                    conn.commit()
                    return self._json({"success": True, "post_id": int(parts[1]), "status": "cancelled"})
                finally:
                    conn.close()
            self._error("Not found", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(str(exc), 500)

    # ── control-plane (jobs) handlers ───────────────────────────────────────--
    def _api_health(self):
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id,type,args,status,started_at FROM jobs "
                "WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            row = None  # jobs table not created yet
        finally:
            conn.close()
        try:
            last_refresh_all = JOBS.estimate_refresh_all(web=False)
        except sqlite3.OperationalError:
            last_refresh_all = None
        sup = supported_depts()
        sup_keys = {d.key for d in sup}
        departments = {
            "supported": [{"key": d.key, "name": d.name} for d in sup],
            "unsupported": [{"key": d.key, "name": d.name}
                            for d in DEPT_REGISTRY.values() if d.key not in sup_keys],
        }
        self._json({
            "status": "ok", "db": str(DB_PATH), "db_exists": DB_PATH.exists(),
            "ollama": _ollama_up(), "running_job": dict(row) if row else None,
            "departments": departments, "last_refresh_all": last_refresh_all,
            "crawl_scope": _crawl_scope(),  # what the KG gather (explore) covers, by college
            "timestamp": utc_now(),
        })

    def _api_get_job(self, raw_id):
        try:
            jid = int(raw_id)
        except (TypeError, ValueError):
            return self._error("bad job id", 400)
        job = JOBS.get_job(jid)
        if job is None:
            return self._error("not found", 404)
        return self._json(job)

    def _api_refresh(self):
        body = self._body()
        # scope=="all" (the "Refresh NJIT KB" button) wins over any department.
        if body.get("scope") == "all":
            try:
                res = JOBS.start_refresh_all(web=bool(body.get("web", False)))
            except JobBusyError:
                return self._error("a job is already running", 409)
            return self._json(res, 201)
        # single-department path (retained for the future)
        dept = str(body.get("department", "cs"))
        if dept not in DEPT_REGISTRY:
            return self._error(f"unknown department: {dept}", 400)
        try:
            limit = int(body.get("limit", 80))
        except (TypeError, ValueError):
            return self._error("limit must be an integer", 400)
        web = bool(body.get("web", False))
        try:
            res = JOBS.start_refresh(department=dept, limit=limit, web=web)
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    def _api_scholar_scopes(self, mode="have"):
        """Dropdown data for the Scholar jobs: All + colleges + departments + eligible counts.
        mode='have' (refresh) = N with Scholar; mode='discover' = N faculty WITHOUT Scholar."""
        conn = self._conn()
        try:
            scopes = _scholar.scholar_scope_list(conn, mode=mode)
        except sqlite3.OperationalError:
            word = "without Scholar" if mode == "discover" else "with Scholar"
            scopes = [{"slug": "", "label": f"All faculty (0 {word})", "type": "all", "eligible": 0}]
        finally:
            conn.close()
        return self._json({"scopes": scopes})

    def _api_discover_scholar(self):
        body = self._body()
        scope = body.get("scope") or None
        if scope is not None:
            conn = self._conn()
            try:
                ok = conn.execute(
                    "SELECT 1 FROM organizations WHERE slug=? AND is_active=1", (scope,)).fetchone()
            except sqlite3.OperationalError:
                ok = None
            finally:
                conn.close()
            if not ok:
                return self._error(f"unknown scope: {scope}", 400)
        try:
            limit = int(body.get("limit", 50))
        except (TypeError, ValueError):
            return self._error("limit must be an integer", 400)
        embed = bool(body.get("embed", True))
        try:
            res = JOBS.start_discover_scholar(scope=scope, limit=limit, embed=embed)
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    def _api_refresh_scholar(self):
        body = self._body()
        scope = body.get("scope") or None     # "" / None ⇒ all faculty with a Scholar URL
        if scope is not None:
            conn = self._conn()
            try:
                ok = conn.execute(
                    "SELECT 1 FROM organizations WHERE slug=? AND is_active=1", (scope,)).fetchone()
            except sqlite3.OperationalError:
                ok = None
            finally:
                conn.close()
            if not ok:
                return self._error(f"unknown scope: {scope}", 400)
        older = body.get("older_than", 30)
        if older in (None, ""):
            older = None
        else:
            try:
                older = int(older)
            except (TypeError, ValueError):
                return self._error("older_than must be an integer", 400)
        embed = bool(body.get("embed", True))
        try:
            res = JOBS.start_refresh_scholar(scope=scope, older_than=older, embed=embed)
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    def _api_explore(self):
        body = self._body()
        try:
            depth = int(body.get("depth", 3))
        except (TypeError, ValueError):
            return self._error("depth must be an integer", 400)
        try:
            res = JOBS.start_explore(depth=depth, frontier=bool(body.get("frontier", False)),
                                     reset=bool(body.get("reset", False)))
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    # NJIT offices the crawler knows how to refresh (SECTIONS keys + 'all').
    _CRAWL_SECTIONS = ("all", "registrar", "financialaid", "graduatestudies", "counseling",
                       "careerservices", "dos", "global", "bursar")

    def _api_crawl_section(self):
        body = self._body()
        section = str(body.get("section", "all"))
        if section not in self._CRAWL_SECTIONS:
            return self._error(f"unknown section: {section}", 400)
        try:
            res = JOBS.start_crawl_section(section=section)
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    # Curated manual rosters the crawler can't reach (JS-rendered / non-profile-template pages).
    _ROSTERS = ("theatre", "senior-administration")

    def _api_seed_roster(self):
        body = self._body()
        roster = str(body.get("roster", "theatre"))
        if roster not in self._ROSTERS:
            return self._error(f"unknown roster: {roster}", 400)
        try:
            res = JOBS.start_seed_roster(roster=roster)
        except JobBusyError:
            return self._error("a job is already running", 409)
        return self._json(res, 201)

    # ── backups (safety net for refreshes) ─────────────────────────────────────
    _BACKUP_DIR = REPO_ROOT / ".backups"

    def _api_list_backups(self):
        """List restore points (newest first): the snapshots every refresh takes."""
        out = []
        if self._BACKUP_DIR.exists():
            for p in sorted(self._BACKUP_DIR.glob("gsa_gateway.*.db"),
                            key=lambda f: f.stat().st_mtime, reverse=True):
                parts = p.name.split(".")          # gsa_gateway.<ts>.<label>.db
                label = parts[2] if len(parts) >= 4 else ""
                st = p.stat()
                out.append({"file": p.name, "label": label,
                            "size_mb": round(st.st_size / 1e6, 1),
                            "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                            .strftime("%Y-%m-%d %H:%M")})
        return self._json({"backups": out})

    @staticmethod
    def _copy_db(src_path, dst_path):
        """Overwrite dst with src via the SQLite backup API, then TRUNCATE the dst WAL so no
        stale frames from a prior connection can replay over the restored content."""
        import sqlite3
        s = sqlite3.connect(str(src_path))
        d = sqlite3.connect(str(dst_path))
        try:
            with d:
                s.backup(d)
            d.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            d.close()
            s.close()

    # The bot processes hold a LONG-LIVED writer connection (bot/services/database.py) and write
    # analytics on every message — so a restore must NOT run while they're up (a concurrent commit
    # during the copy can corrupt/revert the DB). These are their pgrep patterns (cf. restart.sh).
    _BOT_WRITER_PATTERNS = (r"python.*bot\.main", r"python.*run_telegram", r"python.*run_groupme")

    @classmethod
    def _bot_writers_running(cls) -> bool:
        import subprocess
        for pat in cls._BOT_WRITER_PATTERNS:
            if subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0:
                return True
        return False

    @staticmethod
    def _verify_db(path) -> None:
        """Raise if the DB at `path` fails integrity or its vec0 table isn't queryable."""
        from v2.core.database.schema import get_connection
        c = get_connection(str(path))
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("integrity_check failed")
            c.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()   # vec0 smoke test
        finally:
            c.close()

    def _api_restore_backup(self):
        """Restore a chosen backup over the live DB — gated + verified + reversible.

        SAFETY: the bots hold a long-lived WRITER connection, so an online copy while they run can
        corrupt the DB. We therefore refuse unless (a) no refresh job is running AND (b) no bot
        writer process is up. Then: snapshot the current state (reversible) → flush WAL → copy the
        backup in → verify integrity + vec0; on failure, roll back from the snapshot and VERIFY the
        rollback too. File must live in .backups (no traversal)."""
        if not _RESTORE_LOCK.acquire(blocking=False):
            return self._error("a restore is already in progress", 409)
        try:
            return self._do_restore_backup()
        finally:
            _RESTORE_LOCK.release()

    def _do_restore_backup(self):
        if JOBS.is_running():
            return self._error("a refresh is running — wait for it to finish before restoring", 409)
        if self._bot_writers_running():
            return self._error("stop the bots first — they hold the database open, and a live write "
                               "during a restore can corrupt it. Stop them, restore, then run "
                               "scripts/restart.sh.", 409)
        body = self._body()
        fname = str(body.get("file", ""))
        src = (self._BACKUP_DIR / fname).resolve()
        if src.parent != self._BACKUP_DIR.resolve() or not src.exists() or src.suffix != ".db":
            return self._error("unknown backup file", 400)
        import sqlite3
        from scripts._area_tag_migrate import hardened_backup
        try:
            pre = hardened_backup(str(DB_PATH), "pre-restore")   # current state, reversible
        except Exception as exc:  # noqa: BLE001 (disk full etc.) — abort before touching the live DB
            return self._error(f"could not snapshot current state ({exc}); restore aborted", 500)
        # Flush the live WAL so old frames can't replay over the restore, then copy the backup in.
        live = sqlite3.connect(str(DB_PATH))
        try:
            live.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            live.close()
        self._copy_db(src, DB_PATH)
        try:
            self._verify_db(DB_PATH)
        except Exception as exc:  # noqa: BLE001 — roll back, then VERIFY the rollback itself
            try:
                self._copy_db(pre, DB_PATH)
                self._verify_db(DB_PATH)
            except Exception as rexc:  # noqa: BLE001
                logger.exception("restore AND rollback failed")
                return self._error(f"RESTORE FAILED ({exc}) AND ROLLBACK FAILED ({rexc}) — the DB "
                                   f"may be inconsistent. Recover manually from {pre} (and restart).", 500)
            logger.exception("restore verification failed; rolled back to %s", pre.name)
            return self._error(f"restored DB failed verification ({exc}); rolled back to prior state", 500)
        logger.info("restored backup %s (prior state saved to %s)", fname, pre.name)
        return self._json({"restored": fname, "current_saved_to": pre.name})

    def _api_cancel(self, raw_id):
        try:
            jid = int(raw_id)
        except (TypeError, ValueError):
            return self._error("bad job id", 400)
        try:
            return self._json(JOBS.cancel(jid))
        except JobNotFoundError:
            return self._error("not found", 404)

    # ── GET handlers ──────────────────────────────────────────────────────────
    def _get_posts(self, conn, qs):
        limit = int((qs.get("limit") or ["50"])[0])
        where, params = ["1=1"], []
        if qs.get("status"):
            where.append("status=?"); params.append(qs["status"][0])
        rows = conn.execute(
            f"SELECT * FROM posts WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    def _get_orgs(self, conn):
        rows = conn.execute(
            "SELECT id,parent_id,name,slug,type FROM organizations WHERE is_active=1 ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def _get_knowledge(self, conn, qs):
        where, params = ["is_active=1"], []
        if qs.get("org_id"):
            where.append("org_id=?"); params.append(int(qs["org_id"][0]))
        if qs.get("type"):
            where.append("type=?"); params.append(qs["type"][0])
        rows = conn.execute(
            f"SELECT id,org_id,type,title,content,version FROM knowledge_items "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 500", params).fetchall()
        return [dict(r) for r in rows]

    def _get_settings(self, conn, qs):
        if qs.get("org_id"):
            rows = conn.execute("SELECT key,value,type FROM settings WHERE org_id=?",
                                (int(qs["org_id"][0]),)).fetchall()
        else:
            rows = conn.execute("SELECT org_id,key,value,type FROM settings").fetchall()
        return [dict(r) for r in rows]

    def _get_analytics(self, conn, qs):
        days = int((qs.get("days") or ["30"])[0])
        per = f"DATE(timestamp) >= DATE('now','-{days} days')"
        total = conn.execute(f"SELECT COUNT(*) FROM questions WHERE {per}").fetchone()[0]
        answered = conn.execute(f"SELECT COALESCE(SUM(confidence>=50),0) FROM questions WHERE {per}").fetchone()[0]
        users = conn.execute(f"SELECT COUNT(DISTINCT user_id_hash) FROM questions WHERE {per}").fetchone()[0]
        up = conn.execute(f"SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up' AND {per}").fetchone()[0]
        down = conn.execute(f"SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down' AND {per}").fetchone()[0]
        ki = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1").fetchone()[0]
        return {
            "days": days, "total_questions": total, "answered": answered,
            "answer_rate": round(answered / total * 100, 1) if total else 0,
            "unique_users": users, "feedback_up": up, "feedback_down": down,
            "knowledge_items": ki,
        }

    def _send_static(self, filename):
        fp = DASHBOARD_DIR / filename
        if not fp.exists():
            return self._error("Not found", 404)
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(fp.suffix, "text/plain"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # always serve the latest dashboard files
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_db_snapshot(self):
        # WAL-consistent snapshot so the dashboard's read layer sees committed data.
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            src = sqlite3.connect(str(DB_PATH))
            dst = sqlite3.connect(tmp.name)
            src.backup(dst)
            src.close(); dst.close()
            data = Path(tmp.name).read_bytes()
        finally:
            os.unlink(tmp.name)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # ── POST handlers ─────────────────────────────────────────────────────────
    def _post_post(self, conn, b):
        if b.get("type") == "event":
            return self._create_event(conn, b)
        if not b.get("content"):
            raise ValueError("content is required")
        if not b.get("org_id"):
            raise ValueError("org_id is required")
        scheduled = b.get("scheduled_for") or None  # null = send asap
        delete_at = b.get("delete_at") or None       # null = keep forever (auto-delete off)
        if delete_at:                                 # cap at send-time+48h (Telegram ceiling)
            delete_at = _clamp_delete_at(delete_at, scheduled or utc_now())
        cur = conn.execute(
            "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for,"
            "delete_at,status,source_type,signature,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["org_id"], b.get("type", "one_time"), b.get("title"), b["content"],
             json.dumps(b.get("channels", [])), b.get("discord_channel"), scheduled, delete_at,
             b.get("status", "scheduled"), b.get("source_type", "manual"),
             b.get("signature"), b.get("created_by", "dashboard"), utc_now()))
        needs_reindex = False
        if b.get("add_to_kb"):  # also file the post's content as a KB item
            conn.execute(
                "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) "
                "VALUES (?,?,?,?,?,?)",
                (b["org_id"], "announcement", b["content"][:80], b["content"],
                 json.dumps({"source": "post"}), "dashboard"))
            needs_reindex = True
        conn.commit()
        return {"success": True, "post_id": cur.lastrowid, "message": "Post scheduled",
                "needs_reindex": needs_reindex}

    def _create_event(self, conn, b):
        if not b.get("name") or not b.get("date"):
            raise ValueError("event name and date are required")
        now = utc_now()
        cur = conn.execute(
            "INSERT INTO events(org_id,name,date,time,location,description,organizer,category,"
            "created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (b["org_id"], b["name"], b["date"], b.get("time", "TBD"), b.get("location", "TBD"),
             b.get("description", ""), "GSA", "general", now, "dashboard"))
        event_id = cur.lastrowid
        ki = b.get("ki_content") or f"{b['name']} — {b['date']} at {b.get('time','TBD')}, {b.get('location','TBD')}."
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by) VALUES (?,?,?,?,?,?)",
            (b["org_id"], "event_info", b["name"], ki,
             json.dumps({"event_id": event_id, "date": b["date"], "time": b.get("time")}), "dashboard"))
        ann = conn.execute(
            "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for,"
            "status,source_type,source_id,signature,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["org_id"], "event_announcement", b["name"],
             b.get("announce_content") or f"\U0001F4C5 {b['name']} — {b['date']}",
             json.dumps(b.get("channels", [])), b.get("discord_channel"),
             b.get("announce_at") or now, "scheduled", "event", event_id,
             b.get("signature"), "dashboard", now))
        reminders = 0
        for r in (b.get("reminders") or []):
            conn.execute(
                "INSERT INTO event_reminders(event_id,offset_value,offset_unit,channels,enabled,created_at) "
                "VALUES (?,?,?,?,1,?)",
                (event_id, r["offset"], r["unit"], json.dumps(r.get("channels", b.get("channels", []))), now))
            reminders += 1
        conn.commit()
        return {"success": True, "post_id": ann.lastrowid, "event_id": event_id,
                "reminders": reminders, "message": "Event scheduled"}

    def _post_knowledge(self, conn, b):
        if not b.get("content") or not b.get("org_id"):
            raise ValueError("org_id and content are required")
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (b["org_id"], b.get("type", "faq"), b.get("title"), b["content"],
             json.dumps(b.get("metadata", {})), b.get("source_url"), "dashboard"))
        conn.commit()
        return {"success": True, "item_id": cur.lastrowid, "needs_reindex": True}

    _ROLE_TYPE_TO_CATEGORY = {
        "officer": "officer", "dept rep": "deprep", "deprep": "deprep",
        "staff": "staff", "advisor": "advisor", "admin": "admin",
    }

    def _post_person(self, conn, b):
        if not b.get("org_id") or not b.get("name") or not b.get("title"):
            raise ValueError("org_id, name and title are required")
        from v2.core.ingestion.people_editor import add_or_edit_person
        category = GatewayHandler._ROLE_TYPE_TO_CATEGORY.get(str(b.get("role_type", "officer")).lower(), "officer")
        profiles = b.get("profiles")
        if profiles is not None and not isinstance(profiles, dict):
            raise ValueError("profiles must be an object of {field: {url, …}}")
        res = add_or_edit_person(conn, org_id=b["org_id"], name=b["name"], title=b["title"],
                                 category=category, email=b.get("email"), about=b.get("about"),
                                 profiles=profiles)
        conn.commit()
        return {"success": True, "needs_reindex": bool(b.get("about")), **res}

    def _post_person_remove(self, conn, b):
        if not b.get("person_key") or not b.get("org_id"):
            raise ValueError("person_key and org_id are required")
        from v2.core.ingestion.people_editor import remove_person_role
        res = remove_person_role(conn, person_key=b["person_key"], org_id=b["org_id"])
        conn.commit()
        return {"success": True, **res}

    def _post_org(self, conn, b):
        if not b.get("name"):
            raise ValueError("name is required")
        slug = b.get("slug") or b["name"].lower().replace(" ", "-")
        cur = conn.execute(
            "INSERT INTO organizations(parent_id,name,slug,type,description,metadata) VALUES (?,?,?,?,?,?)",
            (b.get("parent_id"), b["name"], slug, b.get("type", "custom"),
             b.get("description"), json.dumps(b.get("metadata", {}))))
        conn.commit()
        return {"success": True, "org_id": cur.lastrowid}

    def _post_setting(self, conn, b):
        if not b.get("key") or b.get("org_id") is None:
            raise ValueError("org_id and key are required")
        key, org_id, value = b["key"], b["org_id"], b.get("value")
        stype = b.get("type", "string")
        if key == "default.auto_delete_hours":
            stype = "int"
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise ValueError("default.auto_delete_hours must be an integer")
            if not (1 <= n <= 48):
                raise ValueError("default.auto_delete_hours must be between 1 and 48")
        # Upsert: the live DB may have no row for a never-seeded key, and a plain UPDATE would
        # silently no-op. Create it if absent, else update in place. (settings has UNIQUE(org_id,key),
        # so the exists-check can't race into a duplicate.)
        exists = conn.execute(
            "SELECT 1 FROM settings WHERE org_id=? AND key=? LIMIT 1", (org_id, key)).fetchone()
        if exists:
            conn.execute(
                "UPDATE settings SET value=?, updated_at=?, updated_by='dashboard' WHERE org_id=? AND key=?",
                (value, utc_now(), org_id, key))
        else:
            conn.execute(
                "INSERT INTO settings(org_id,key,value,type,updated_by) VALUES (?,?,?,?,'dashboard')",
                (org_id, key, value, stype))
        conn.commit()
        return {"success": True}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not DB_PATH.exists():
        logger.error("Database not found: %s", DB_PATH)
        sys.exit(1)
    # Self-heal the schema: apply any new tables/indexes idempotently (e.g. the judging
    # audit table) so a standalone server stays in sync even when the bot hasn't restarted.
    from v2.core.database.schema import create_all as _create_all
    _create_all(str(DB_PATH)).close()
    JOBS.ensure_schema()       # create the jobs table if missing (backend owns it)
    JOBS.reconcile_startup()   # any 'running' row from a prior process → interrupted
    server = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    logger.info("GSA Gateway local server")
    logger.info("Listening on http://%s:%d  (localhost only)", HOST, PORT)
    logger.info("Database: %s", DB_PATH)
    logger.info("From your laptop:")
    logger.info("  1) ssh -L %d:localhost:%d md724@<server-ip>", PORT, PORT)
    logger.info("  2) open http://localhost:%d/ in your browser (it auto-connects)", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped")
        server.server_close()


if __name__ == "__main__":
    main()

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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# The live db lives at the repo root (this file is v2/local_server.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "gsa_gateway.db"
DASHBOARD_DIR = REPO_ROOT / "dashboard"          # served so one URL = whole app
HOST = "127.0.0.1"   # localhost ONLY — reachable only via SSH tunnel
PORT = int(os.environ.get("GSA_SERVER_PORT", "5555"))  # override for testing/alt ports

# Static dashboard files served from the same origin (no CORS, one tunnel).
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
          "/style.css": "style.css", "/posts_logic.js": "posts_logic.js"}
CONTENT_TYPES = {".html": "text/html", ".js": "application/javascript",
                 ".css": "text/css"}

logger = logging.getLogger("local_server")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
        return json.loads(self.rfile.read(length).decode() or "{}")

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ────────────────────────────────────────────────────────────────---
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path in STATIC:
                return self._send_static(STATIC[path])
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
            self._error("Not found", 404)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GET %s failed", path)
            self._error(str(exc), 500)

    # ── POST ───────────────────────────────────────────────────────────────---
    def do_POST(self):
        path = urlparse(self.path).path
        try:
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
            finally:
                conn.close()
            self._error("Not found", 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s failed", path)
            self._error(str(exc), 500)

    # ── DELETE ─────────────────────────────────────────────────────────────---
    def do_DELETE(self):
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
        cur = conn.execute(
            "INSERT INTO posts(org_id,type,title,content,channels,discord_channel,scheduled_for,"
            "status,source_type,signature,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["org_id"], b.get("type", "one_time"), b.get("title"), b["content"],
             json.dumps(b.get("channels", [])), b.get("discord_channel"), scheduled,
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
        conn.execute(
            "UPDATE settings SET value=?, updated_at=?, updated_by='dashboard' WHERE org_id=? AND key=?",
            (b.get("value"), utc_now(), b["org_id"], b["key"]))
        conn.commit()
        return {"success": True}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not DB_PATH.exists():
        logger.error("Database not found: %s", DB_PATH)
        sys.exit(1)
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

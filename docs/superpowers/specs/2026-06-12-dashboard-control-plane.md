# Dashboard Control Plane — Design Spec

**Goal:** Let the admin trigger and monitor long-running jobs (faculty refresh first;
later index rebuild, KB-patch apply) **entirely from the dashboard — no terminal**,
while keeping the dashboard itself thin and adding the least possible new infrastructure.

**Principle:** the dashboard stays the UI ("the face"); a tiny control layer **inside
the localhost backend the dashboard already talks to** is "the hands". No new daemon.

> **Decision (2026-06-13, after senior review):** the job endpoints live in the
> **existing `v2/local_server.py`** — the localhost backend that already serves the
> dashboard + the live-DB read/write path on `127.0.0.1:5555`. The original draft
> proposed a *second* aiohttp server inside the bot; review found that collides with
> `local_server.py` on the same port with an opposite CORS model, and would force a
> cross-origin Jobs panel. Folding jobs into `local_server.py` keeps **one backend,
> one origin** (clean security, no CORS contortions). To make it **always-on** without
> a new systemd unit (a clean-install admin shouldn't have to authorize a service),
> the **already-always-on bot supervises `local_server.py` as a child process** — so
> whenever the bot is up (which is the always-on requirement already), the dashboard
> backend is up too. Job subprocesses are independent processes (own `python`, Ollama
> over HTTP), so nothing runs on the bot's event loop.

---

## 1. Why in `local_server.py` (not the bot, not a new server)

The dashboard is a browser app (`sql.js`); a browser can't crawl sites or call Ollama.
`local_server.py` is the localhost backend the dashboard already auto-connects to over
the SSH tunnel — it serves the static dashboard *and* the live DB. Adding the job
endpoints there means the Jobs panel is **same-origin** with everything else (no CORS,
one tunnel, one port) and sits naturally next to the DB read/write path.

The bot stays focused on Discord/Telegram. Its only new responsibility is **launching
`local_server.py` as a supervised child** so the backend is always-on for free.

## 2. What the backend serves (unchanged + additive)

`local_server.py` already serves, on `127.0.0.1:5555`:
1. the static `dashboard/` files (`/`, `/app.js`, …),
2. the live-DB read/write API (`/db`, `/posts`, `/knowledge`, `/orgs`, `/settings`, …).

This spec **adds** an `/api/*` control plane next to it. The existing endpoints and the
dashboard's editing flow are **untouched**. The Jobs panel is purely additive.

## 3. API (localhost-only, browser-attack-guarded — no pasted token in v1)

A token would authenticate *the admin*, who already has SSH/code access — pointless
friction on a single-user box. The real threats to a localhost job-runner are **other
web pages** the admin's browser visits (CSRF / DNS-rebinding). v1 closes those without a
token:

- Bind `127.0.0.1` only (already true — off-machine is unreachable; SSH provides auth).
- **Host-header allowlist** (`localhost:<port>` / `127.0.0.1:<port>`) on **all** routes —
  defeats DNS-rebinding for reads and writes alike.
- **Origin check + a required custom header** (`X-GSA-Dashboard: 1`) on every
  state-changing `/api/*` call — browsers can't set a custom header cross-origin without
  a CORS preflight we never grant, so other sites can't trigger jobs. (`Origin: null`
  from `file://` is allowed; same-origin requests send no `Origin`.)

Add `DASHBOARD_TOKEN` later, when this moves to the multi-user NJIT server or once
destructive endpoints (KB-patch-apply) land.

| Method & path | Purpose |
|---|---|
| `GET /api/health` | backend up? ollama up? db path, current running job (if any) |
| `POST /api/jobs/refresh` `{department, limit?, web?}` | start a faculty refresh; → `{job_id}`; **409** if a job is already running |
| `GET /api/jobs` | recent jobs: id, type, status, started, one-line summary |
| `GET /api/jobs/{id}` | full status + tail of the job log + summary |
| `POST /api/jobs/{id}/cancel` | terminate the running job's process group |

Future (same shape, later phase): `POST /api/index/rebuild`, `POST /api/kb/apply-patch`.

## 4. Job execution

- `POST /api/jobs/refresh` spawns **`ingest_faculty.py` directly** (not the wrapper
  script) so flags are honorable:
  `\.venv/bin/python scripts/ingest_faculty.py --department <d> --limit <n> --overview --commit [--web]`
  with an explicit `cwd=<repo root>`, explicit `--db <config.database_path>` (so the child
  and the live bot provably target the same file), and `start_new_session=True` (its own
  process group). Run via `subprocess` — it is an independent process, never on an event
  loop. *(The old `refresh_faculty.sh` hardcoded `--web --commit --overview`, so the spec's
  `web` toggle was a lie; calling the script directly fixes that.)*
- Output (stdout+stderr) streams to `logs/jobs/<job_id>.log`; the API tails it for live
  progress. `logs/jobs/` is created on startup.
- **One job at a time** (a single in-process lock). A second trigger returns 409. The lock
  serializes *heavy ingest runs* against each other — it does **not** (and cannot) prevent
  the live bot/scheduler from also writing; that's safe today via WAL + `busy_timeout`,
  which the child inherits.
- The ingest path already does **auto-backup → crawl → extract → overview → embed →
  per-entity commit → change-log** (`logs/ingest_changes.log`), so triggering via the API
  gets all the safety for free. It commits per entity, so a cancel stops cleanly at the
  next entity boundary.
- On exit: status `done` (exit 0) / `failed`; the summary is parsed from the job's own log
  tail.

**Persistence:** a `jobs` table (`id, type, args, status, started_at, finished_at,
log_path, pid, summary`). It is created with `CREATE TABLE IF NOT EXISTS` by
`local_server.py` itself at startup — **not** in `v2/core/database/schema.py`, because the
bot never runs that schema's `create_all`; the backend owns the table it writes. Live
status is served from memory; history from the table.

**Restart safety:** on startup the backend reconciles any `status='running'` rows to
`interrupted` (their in-memory watcher is gone). Detached job subprocesses may keep
running, but we no longer track them — the honest state is "interrupted / lost track".

## 5. The dashboard "Jobs" panel (thin)

A new tab. Minimal UI:
- Buttons: **Refresh CS**, **Refresh DS** (DS greyed with tooltip "JS-rendered — not yet
  supported"), an **include personal websites** toggle (`web`) that is now honored.
- On click → `POST /api/jobs/refresh` → poll `GET /api/jobs/{id}` every ~3 s → show
  status + live log tail; on done show the summary and the change-log.
- A small **recent jobs** list with status.
- Sends the `X-GSA-Dashboard: 1` header on state-changing calls (the CSRF guard). No token.

No `sql.js` changes; the panel is plain fetch calls to the same origin.

## 6. Security

- Bind `127.0.0.1` only (already so) — never reachable off the machine; SSH is the auth.
- Host-header allowlist on all routes (defeats DNS-rebinding) + Origin check + required
  `X-GSA-Dashboard` header on state-changing `/api/*` calls (defeats cross-site
  triggering). See §3.
- Log endpoints read `log_path` from the `jobs` row keyed by an integer `{id}` — never
  interpolate a user value into a filesystem path (no `../` traversal); the tail is
  bounded (last ~16 KB), never the whole file.
- Reuses the ingest path's safety (verified auto-backup, the SSRF-guarded crawler).
- Cancel terminates the **process group** (`os.killpg`, SIGTERM), not just the leader pid,
  so the python child and its workers die together.
- Config flags (bot side): `DASHBOARD_SERVER_ENABLED` (default off), `DASHBOARD_SERVER_PORT`
  (default 5555 → passed to the child as `GSA_SERVER_PORT`).
  (`DASHBOARD_TOKEN` added later for the multi-user server / destructive endpoints.)

## 7. Files

- `bot/services/jobs.py` — `JobManager`: the schema, lock, subprocess spawn/cancel,
  reconciliation, log tail. Framework-agnostic and unit-tested.
- `v2/local_server.py` — mount the `/api/*` routes on the existing handler; init the
  `JobManager` (ensure schema + reconcile) at startup; add the Host/Origin/header guards.
- `bot/main.py` — supervise `local_server.py` as a child in `setup_hook`, stop it in
  `close()`, gated by `DASHBOARD_SERVER_ENABLED`.
- `bot/config.py` — the new config flags.
- `dashboard/index.html` + `app.js` — the thin Jobs tab.

## 8. Scope

**v1:** faculty-refresh trigger + status + change-log view; one job at a time; in
`local_server.py`, supervised by the bot, localhost, browser-attack-guarded (no token).
**Not v1:** the KB-patch-apply and index-rebuild endpoints (same pattern, added once v1 is
proven); multi-job queue; remote access; re-attaching to a job that survived a restart.

## 9. Out of scope / non-goals

- No exposure beyond localhost. No multi-tenant auth. No new systemd unit. No replacing the
  dashboard's existing editing model — only adding job control next to it.

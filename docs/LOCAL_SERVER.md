# GSA Gateway Local Server

`v2/local_server.py` is a tiny stdlib HTTP server that lets the dashboard
read/write the **live** `gsa_gateway.db` directly — no file download/upload, no
manual SQL copy-paste. Writes apply immediately; the v2 scheduler picks up new
posts within ~30 seconds.

## Setup (3 steps)

**1. On the server** (where the bot runs), start it.

> **Recommended — let the bot run it.** Set `DASHBOARD_SERVER_ENABLED=true` in
> `.env` and the bot launches this server as a supervised child on startup (and
> stops it on shutdown), so it is always-on with no separate process to remember.
> See **[Always-on via the bot](#always-on-via-the-bot)** below. When that is on,
> do **not** also start it by hand — both would try to bind the same port.

For dev, or if you prefer to run it manually:

```bash
cd ~/gsa-gateway
.venv/bin/python v2/local_server.py
# keep it running:  nohup .venv/bin/python v2/local_server.py >/dev/null 2>&1 &
# (if 5555 is busy:  GSA_SERVER_PORT=5556 .venv/bin/python v2/local_server.py)
```

**2. On your laptop**, open an SSH tunnel:

```bash
ssh -L 5555:localhost:5555 md724@<your-server-ip>
```

**3. In your browser**, open:

```
http://localhost:5555/
```

The server hosts the dashboard itself, so this one URL is the whole app. It
**auto-connects** — the status pill shows **● server (read/write)**. No files to
copy, no buttons to click.

## What you can do in server mode

- ✅ Create and schedule posts (one-time and events)
- ✅ Add knowledge base content
- ✅ Add organization nodes
- ✅ Edit settings
- ✅ All changes apply to the live database immediately
- ✅ The scheduler delivers posts within ~30–60s

After adding **knowledge base** content, run the reindex so the bot can find it
(it embeds via Ollama and rebuilds FTS):

```bash
python v2/scripts/rebuild_index.py
```

## API (localhost only)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"ok","db_exists":true,...}` |
| GET | `/db` | consistent snapshot (the dashboard's read layer) |
| GET | `/posts?limit=&status=` | list posts |
| GET | `/orgs` | organizations |
| GET | `/knowledge?org_id=&type=` | knowledge items |
| GET | `/settings?org_id=` | settings |
| GET | `/analytics?days=` | aggregated stats |
| POST | `/posts` | create post (or event → events + reminders) |
| POST | `/knowledge` | create knowledge item (`needs_reindex: true`) |
| POST | `/orgs` | add organization node |
| POST | `/settings` | update a setting |
| DELETE | `/posts/{id}` | cancel a post |

Times are stored as **UTC**; the dashboard converts to/from `org.timezone`.

## Control plane — the Jobs API

The same server exposes an `/api/*` control plane so the admin can **trigger and
monitor long-running jobs from the dashboard, with no terminal**. v1 covers the
**faculty knowledge-base refresh**; the index-rebuild and KB-patch-apply jobs use
the same shape and land in a later phase.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | backend up? `ollama` up? db path + the current running job (if any) |
| GET | `/api/jobs` | recent jobs: id, type, status, started, one-line summary |
| POST | `/api/jobs/refresh` | start a faculty refresh — body `{department, limit?, web?}`; → `{job_id}`; **409** if a job is already running |
| GET | `/api/jobs/{id}` | full status + a tail of the job log + summary |
| POST | `/api/jobs/{id}/cancel` | terminate the running job's process group |

**How a job runs.** A refresh spawns `scripts/ingest_faculty.py` directly as an
independent, detached subprocess (`--department <d> --limit <n> --overview
--commit [--web]`), never on the bot's event loop. The ingest pipeline takes a
**verified auto-backup first** (`.backups/`), then crawl → extract → overview →
embed → per-entity commit, writing the diff to `logs/ingest_changes.log`. Output
streams to `logs/jobs/<job_id>.log`. **One job at a time** — a second trigger
gets a 409.

- **`web`** includes each professor's personal website (much slower — a per-prof
  time budget — but more complete). Off by default from the API.
- **`department`** must be a registry key: `cs` (works), `ds` / `informatics`
  (DS profiles are JS-rendered and not yet supported — the dashboard greys it).
- **History** lives in a `jobs` table (created by this server at startup). On
  startup any job left `running` by a previous process is reconciled to
  `interrupted` (we can no longer track a detached child across a restart).
- **Cancel** sends `SIGTERM` to the whole process group; the ingest commits
  per-entity, so it stops cleanly at the next entity boundary.

### Security (control plane)

On top of the localhost/SSH model below, the `/api/*` routes add browser-attack
guards (the real threat to a localhost job-runner is other web pages the admin's
browser visits — there is **no token** in v1, by design, on a single-user box):

- **Host-header allowlist** on every route (`localhost:<port>` / `127.0.0.1:<port>`)
  — defeats DNS-rebinding.
- **Required `X-GSA-Dashboard: 1` header + Origin check** on every state-changing
  `/api/*` call — a cross-site page can't set a custom header without a CORS
  preflight we never grant, so other sites can't trigger jobs.

A `DASHBOARD_TOKEN` will be added when this moves to the multi-user NJIT server or
when destructive endpoints (KB-patch-apply) land.

## Always-on via the bot

Set in `.env`:

```
DASHBOARD_SERVER_ENABLED=true     # bot launches this server as a child on startup
DASHBOARD_SERVER_PORT=5555        # passed to the child as GSA_SERVER_PORT
```

Restart the bot, then confirm:

```bash
journalctl -u gsa-gateway -f | grep -i dashboard
# → "Dashboard control server launched (pid …, port 5555)"
curl -s http://127.0.0.1:5555/api/health; echo
```

The backend now comes up and down **with the bot** — no manual start, no extra
systemd unit. Stopping the bot stops the child; starting it brings it back.

## Verifying it works (runbook)

Run these in order before relying on the control plane (e.g. after deploying to a
new machine). Stages A–B are read-only and safe; C does a real one-professor
refresh (protected by the auto-backup).

**A. Unit + integration tests**

```bash
.venv/bin/python -m pytest bot/tests/test_jobs.py bot/tests/test_control_api.py -v
# expect 25 passing
```

**B. Boot the backend and check the API + guards** (use a throwaway port so it
can't clash with a running instance):

```bash
GSA_SERVER_PORT=5599 .venv/bin/python v2/local_server.py     # leave running
# in another terminal:
curl -s http://127.0.0.1:5599/api/health; echo                # ollama:true, running_job:null
curl -s http://127.0.0.1:5599/api/jobs;   echo                # {"jobs": []}

# guards — all three must print 403:
curl -s -o /dev/null -w "no-header:  %{http_code}\n" \
  -X POST http://127.0.0.1:5599/api/jobs/refresh -d '{"department":"cs"}'
curl -s -o /dev/null -w "bad-host:   %{http_code}\n" \
  -H "Host: evil.com" http://127.0.0.1:5599/api/health
curl -s -o /dev/null -w "bad-origin: %{http_code}\n" \
  -H "X-GSA-Dashboard: 1" -H "Origin: http://evil.com" \
  -X POST http://127.0.0.1:5599/api/jobs/refresh -d '{"department":"cs"}'
```

**C. One real, small refresh** (one professor — proves the whole pipeline; the
ingest auto-backs-up the DB first):

```bash
curl -s -H "X-GSA-Dashboard: 1" -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:5599/api/jobs/refresh \
  -d '{"department":"cs","limit":1,"web":false}'; echo          # → {"job_id":N,...}

# watch it: status should go running → done
watch -n 3 'curl -s http://127.0.0.1:5599/api/jobs/N | python3 -m json.tool | head -40'

tail -n 20 logs/jobs/N.log            # the job's own output
tail -n 20 logs/ingest_changes.log    # the per-entity audit diff
ls -lt .backups/ | head -3            # the auto-backup taken before writing
```

Test **cancel** by starting a long run and cancelling it:

```bash
curl -s -H "X-GSA-Dashboard: 1" -X POST http://127.0.0.1:5599/api/jobs/refresh \
  -d '{"department":"cs","limit":80,"web":true}'                # → job_id M
curl -s -H "X-GSA-Dashboard: 1" -X POST http://127.0.0.1:5599/api/jobs/M/cancel; echo
curl -s http://127.0.0.1:5599/api/jobs/M | python3 -m json.tool # status: cancelled
```

Ctrl-C the throwaway server when done, then enable the always-on path above.

## Security

- Binds to **127.0.0.1 only** — never `0.0.0.0`.
- Only reachable through the SSH tunnel; **SSH provides authentication**.
- Never exposed to the internet.
- `/api/*` job routes add a Host allowlist + CSRF header/Origin check (see
  [Control plane](#control-plane--the-jobs-api)).

## Fallback (file mode)

If you can't use a tunnel, click **Load database file** instead. Reads work via
`sql.js`; each write produces a SQL patch you apply with
`sqlite3 gsa_gateway.db < changes.sql` (see `DASHBOARD.md`).

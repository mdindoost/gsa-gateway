# GSA Gateway Local Server

`v2/local_server.py` is a tiny stdlib HTTP server that lets the dashboard
read/write the **live** `gsa_gateway.db` directly — no file download/upload, no
manual SQL copy-paste. Writes apply immediately; the v2 scheduler picks up new
posts within ~30 seconds.

## Setup (3 steps)

**1. On the server** (where the bot runs), start it:

```bash
cd ~/gsa-gateway
python v2/local_server.py
# keep it running:  nohup python v2/local_server.py >/dev/null 2>&1 &
# (if 5555 is busy:  GSA_SERVER_PORT=5556 python v2/local_server.py)
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

## Security

- Binds to **127.0.0.1 only** — never `0.0.0.0`.
- Only reachable through the SSH tunnel; **SSH provides authentication**.
- Never exposed to the internet.

## Fallback (file mode)

If you can't use a tunnel, click **Load database file** instead. Reads work via
`sql.js`; each write produces a SQL patch you apply with
`sqlite3 gsa_gateway.db < changes.sql` (see `DASHBOARD.md`).

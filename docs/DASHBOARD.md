# GSA Gateway v2 — Admin Dashboard

A local, serverless admin UI (`dashboard/index.html`). It reads your SQLite
database in the browser via `sql.js` (WebAssembly) — nothing is uploaded
anywhere. It is a **visual editor**: it never writes the live database directly.
Every change you make produces a small **SQL patch** that you apply from the
terminal. This keeps the live, bot-open database safe (a full-file overwrite
while the bot is running would clobber its WAL).

## Loading

1. Open `dashboard/index.html` in Chrome.
2. Click **Load Database** and pick your `gsa_gateway.db` (a copy is fine).

## Creating posts and adding knowledge

1. Use the dashboard to fill in your content, schedule, channels and settings.
2. Click **Schedule Post** (Posts tab) or **Save Content** (Knowledge Base tab).
3. A dialog shows a generated SQL patch. Click **📋 Copy SQL** (or **⬇ Download
   changes.sql**).
4. Apply it to the live database in your terminal:
   ```bash
   sqlite3 gsa_gateway.db < changes.sql
   ```
5. **For Knowledge Base changes**, also rebuild the search index so the bot can
   find the new content (it embeds via Ollama and rebuilds FTS):
   ```bash
   python v2/scripts/rebuild_index.py
   ```

The v2 scheduler (running in the bot) picks up new posts within ~30 seconds and
delivers them to Discord/Telegram, logging each delivery to `post_deliveries`.

Every action that changes data — new post, event, knowledge item, organization
node, cancel/resend, settings — produces a patch the same way. The dashboard
also applies the change to its in-memory copy so the UI updates immediately.

## Timezone

All times are stored in the database as **UTC**. The dashboard shows and accepts
times in your **organization timezone** (`org.timezone`, default
`America/New_York`). When you type a send time it shows the equivalent UTC
("= 2026-06-12 22:00 UTC") and the SQL patch stores UTC. Lists and details
display the org-local time (e.g. "Jun 12, 6:00 PM EDT"). This lets an admin in
one timezone schedule correctly for a server in another.

## Tabs

- **Overview** — stats, recent/upcoming posts, system health.
- **Posts** — list + 3-mode creation form (one-time / recurring / event) with
  live Discord/Telegram preview and signature.
- **Knowledge Base** — org tree, content list, add/edit with version history;
  add organization nodes.
- **Analytics** — questions, feedback, posts, KB stats with charts.
- **Settings** — organization info, signature (+ live preview), channels,
  notification defaults, retriever tuning, features, platform config.
- **Jobs** — trigger and monitor long-running jobs (faculty KB refresh).
  *Server mode only* (see below).

## Jobs — refreshing the faculty knowledge base

The **Jobs** tab lets you refresh the faculty knowledge base **from the browser,
no terminal**. It only works in **server mode** (connected to `local_server.py`
over the tunnel) — a database loaded as a file has no backend to run the job, so
the tab shows a note to connect instead.

1. Connect in server mode and open the **🛠️ Jobs** tab.
2. Check the header: **Ollama ● up** (overviews/embeddings need it). If it's
   down, **Refresh CS** is disabled until you start Ollama.
3. Optionally tick **include personal websites** (more complete, much slower).
4. Click **Refresh CS**. (**Refresh DS** is greyed — those profiles are
   JS-rendered and not yet supported.)
5. A job card shows the live log (polled every ~3s) and a **Cancel** button while
   running; on completion it shows a one-line summary. The **Recent jobs** list
   below keeps history — click a row to re-open its log.

Behind the scenes the job auto-backs-up the database, crawls, extracts, writes
grounded overviews, embeds, and logs the exact diff to `logs/ingest_changes.log`.
Only one job runs at a time. Full details + the API: see `LOCAL_SERVER.md`.

> **Always-on:** with `DASHBOARD_SERVER_ENABLED=true` the bot runs the backend
> for you, so the Jobs tab is available whenever the bot is up. See
> `LOCAL_SERVER.md` → *Always-on via the bot*.

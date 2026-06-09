<div align="center">

# GSA Gateway

**A Discord + Telegram assistant and admin platform for NJIT's Graduate Student Association.**

GSA Gateway answers graduate students' questions about events, funding, policies, and
campus resources through free-form chat — grounded in a curated knowledge base via a
local Retrieval-Augmented Generation (RAG) pipeline, so it never makes things up.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2.svg)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#testing)

[**🌐 Website**](https://mdindoost.github.io/gsa-gateway/) ·
[**💬 Discord**](https://discord.gg/a4mvbEmSAq) ·
[**✈️ Telegram**](https://t.me/njit_gsa_bot) ·
[**📖 Docs**](docs/)

</div>

---

## What it does

- **Conversational Q&A** on Discord (`#ask-gsa` and DMs) and Telegram — ask anything about
  the GSA in plain language and get answers grounded in the official knowledge base.
- **Slash commands** for events, funding, resources, contacts, feedback, and more.
- **Scheduled & broadcast posts** to both platforms from one place (announcements, events,
  reminders, a live World Cup match tracker).
- **A local admin dashboard** to manage the knowledge base, posts, organizations, and
  settings — no SQL required.
- **Privacy-first:** user IDs are hashed before storage; feedback is anonymous by default.

## Student commands

### Discord

| Command | What it does |
|---|---|
| `/ask` | AI-powered Q&A from the GSA knowledge base (RAG, with conversation memory) |
| `/events` · `/event [name]` | List upcoming events, or full detail for one |
| `/resources [category]` | Browse curated student resources |
| `/contact [role]` | Look up GSA officers and key NJIT offices |
| `/initiative` | Submit a student idea or initiative (anonymous by default) |
| `/feedback` | Send a private, anonymous note to GSA officers |
| `/worldcup` | World Cup 2026 schedule and info |
| `/qrcode` | Generate a QR code (e.g. for event sign-ups) |
| `/help` | Full command reference |

Plus **free-form chat** in `#ask-gsa` and direct messages.

### Telegram
The same knowledge-base Q&A is available by chatting with [**@njit_gsa_bot**](https://t.me/njit_gsa_bot).

### Admin (officers only, ephemeral)
`/admin_stats` · `/admin_summary` · `/admin_export` · `/admin_announce` · `/admin_add_event` · `/admin_rebuild_index`

---

## Architecture

GSA Gateway runs as a single bot process with a layered design:

- **The bot (`bot/`)** — the running Discord + Telegram application: commands, free-form
  chat, intent routing, reminders, daily digest, and the World Cup tracker.
- **The retrieval pipeline** — documents are chunked, embedded (`nomic-embed-text` via
  Ollama), and stored in a vector index. Each question is embedded, matched against the
  index, reranked, and answered by a local LLM (`llama3.1:8b`) **grounded only in the
  retrieved context**. If the LLM is unavailable, it degrades gracefully to fuzzy search.
- **The v2 platform (`v2/`)** — a database-first core that everything is converging on:
  a single SQLite database (with `sqlite-vec` + FTS5 hybrid retrieval), an organization
  hierarchy, versioned knowledge items, a universal *posts* model, and a **connector
  pattern** that fans one message out to every platform in parallel. Feature-flagged so it
  can be toggled on per capability with instant rollback.
- **The dashboard (`dashboard/`)** — a dependency-free admin UI (see below).
- **The website (`website/`)** — a static, GitHub Pages–ready info site.

> A deeper write-up lives in [`docs/architecture.md`](docs/architecture.md).

## Tech stack

Python 3.11+ · [discord.py](https://discordpy.readthedocs.io/) 2.x ·
[python-telegram-bot](https://python-telegram-bot.org/) · SQLite ·
[sqlite-vec](https://github.com/asg017/sqlite-vec) + FTS5 ·
[Ollama](https://ollama.com/) (local LLM + embeddings) ·
[rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) · pytest.
The dashboard is pure HTML/CSS/JS (sql.js, Chart.js via CDN).

---

## Quick start

**Prerequisites:** Python 3.11+, a Discord bot token, and (optional but recommended)
[Ollama](https://ollama.com/) running locally for AI answers.

```bash
git clone https://github.com/mdindoost/gsa-gateway.git
cd gsa-gateway

# one-time setup: venv, dependencies, database, tests
bash scripts/setup.sh

# configure secrets
cp .env.example .env        # then edit: DISCORD_TOKEN, etc.

# pull the local models (if using Ollama)
ollama pull llama3.1:8b
ollama pull nomic-embed-text

# build the search index, then start the bot
python scripts/build_index.py --reset
bash scripts/restart.sh                 # add --no-llm to run without Ollama
```

Watch it come up with `tail -f gsa_gateway.log`.

## Admin dashboard

A local, serverless admin UI for managing posts, the knowledge base, organizations, and
settings. Two ways to use it:

- **Server mode (recommended):** run `python v2/local_server.py` on the host, open an SSH
  tunnel (`ssh -L 5555:localhost:5555 user@host`), and visit **`http://localhost:5555/`** —
  it hosts the dashboard and reads/writes the live database directly.
- **File mode:** open `dashboard/index.html` and load a database copy; changes are exported
  as SQL patches you apply manually.

Full guide: [`docs/LOCAL_SERVER.md`](docs/LOCAL_SERVER.md) and [`docs/DASHBOARD.md`](docs/DASHBOARD.md).

---

## Project structure

```
gsa-gateway/
├── bot/            Discord + Telegram bot — commands, services (RAG, search, DB), data
├── v2/             Database-first platform — schema, retrieval, publishing, connectors,
│                   integration shims, local admin server, tests
├── dashboard/      Serverless admin UI (HTML/CSS/JS)
├── website/        Static GitHub Pages site
├── scripts/        setup.sh, restart.sh, build_index.py, exports, migrations
└── docs/           Architecture, deployment, admin & student guides, privacy policy
```

## Configuration

All configuration is via `.env` (see `.env.example`). Key settings:

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN`, `TELEGRAM_BOT_TOKEN` | Platform credentials |
| `ADMIN_ROLE_NAME` | Discord role allowed to run admin commands |
| `ALLOWED_CHANNELS` | Comma-separated channel names the bot listens in |
| `OLLAMA_ENABLED`, `OLLAMA_MODEL` | Local LLM for AI answers |
| `V2_RETRIEVER_ENABLED`, `V2_SCHEDULER_ENABLED`, `V2_WORLDCUP_ENABLED` | Feature flags for the v2 platform (instant rollback) |

## Testing

```bash
pytest                  # full suite
pytest v2/tests/ -v     # v2 platform
```

## Documentation

| Doc | For |
|---|---|
| [`docs/MANUAL.md`](docs/MANUAL.md) | Running, maintaining, and extending the project |
| [`docs/architecture.md`](docs/architecture.md) | System design |
| [`docs/admin_guide.md`](docs/admin_guide.md) | Officer / admin operations |
| [`docs/student_usage_guide.md`](docs/student_usage_guide.md) | Student-facing how-to |
| [`docs/deployment.md`](docs/deployment.md) | Deployment & server migration |
| [`docs/DASHBOARD.md`](docs/DASHBOARD.md) · [`docs/LOCAL_SERVER.md`](docs/LOCAL_SERVER.md) | Admin dashboard |
| [`docs/privacy_policy.md`](docs/privacy_policy.md) | Data & privacy |

---

## Contributing

Issues and pull requests are welcome. Please run `pytest` before submitting, and keep new
code consistent with the surrounding style. For larger changes, open an issue first to
discuss the approach.

## Maintainer

Built and maintained by **Mohammad Dindoost** (VP Academic Affairs, NJIT GSA).

## License

[MIT](LICENSE) © 2026 NJIT Graduate Student Association

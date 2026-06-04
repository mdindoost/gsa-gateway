# GSA Gateway

**Discord bot + Telegram bot + static website for NJIT's Graduate Student Association.**

GSA Gateway is a full RAG (Retrieval-Augmented Generation) conversational AI assistant that makes GSA information, events, funding, and resources accessible to all NJIT graduate students through free-form chat on Discord and Telegram.

**Live website:** https://mdindoost.github.io/gsa-gateway/
**GitHub repo:** https://github.com/mdindoost/gsa-gateway
**Discord:** https://discord.gg/a4mvbEmSAq
**Telegram:** https://t.me/njit_gsa_bot

> For how to run, maintain, and extend this project — see **[MANUAL.md](MANUAL.md)**.

---

## Student Commands

### Discord

| Command | What it does |
|---|---|
| `/ask` | AI-powered Q&A from the GSA knowledge base (llama3.1:8b via Ollama RAG). Supports follow-up questions with conversation memory. |
| `/events` | List all upcoming GSA events, sorted by date |
| `/event [name]` | Full details for a specific event |
| `/initiative` | Submit a student initiative or idea (anonymous by default) |
| `/feedback` | Send a private anonymous message to GSA officers |
| `/resources [category]` | Browse 8 categories of curated student resources |
| `/contact [role]` | Look up GSA officers and key NJIT campus offices |
| `/help` | Full command reference |

### Telegram (@njit_gsa_bot)

| Command | What it does |
|---|---|
| `/events` | Upcoming GSA events with date, time, and location |
| `/contact [role]` | GSA officers and their contact info |
| `/resources [category]` | Curated campus resources by category |
| `/help` | How to use the bot |
| _(free text)_ | Ask any question — same RAG pipeline as Discord |

---

## Officer Commands (Discord only)

| Command | What it does |
|---|---|
| `/admin_add_event` | Add an event via a form — auto-posts announcement & schedules reminders. Events with `food` tag post a "🍕 FREE FOOD ALERT!" to `#gsa-food`. |
| `/admin_announce` | Post an announcement embed to any channel |
| `/admin_summary` | AI-generated weekly summary of student submissions |
| `/admin_export` | Download CSV of initiatives, feedback, or questions |
| `/admin_stats` | Engagement stats, RAG chunk count, active conversation sessions |
| `/admin_rebuild_index` | Rebuild the ChromaDB vector index after editing KB files |

---

## How the Announcement System Works

When an officer runs `/admin_add_event`, the bot automatically:
1. Posts a green "NEW EVENT" embed to the matching category channel and `#gsa-announcements`
2. Schedules a **7-day reminder** (blue embed), **1-day reminder** (orange), and **1-hour reminder** (red)
3. Saves the event to SQLite and `events.yml`
4. Updates `website/data/events.json` for the public website

A **daily digest** posts to `#gsa-announcements` every morning at 9 AM if events are coming up that week.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Discord bot | Python 3.11+, discord.py 2.x, SQLite, rapidfuzz |
| Telegram bot | python-telegram-bot 20.x |
| RAG | ChromaDB (vector store), nomic-embed-text (embeddings), llama3.1:8b (generation) |
| AI | Ollama (local — no API costs, no data leaves the machine) |
| Website | Pure HTML/CSS/JS, GitHub Pages |
| Tests | pytest, 212+ tests |
| Process management | systemd |

### Free-form Chat
Both Discord and Telegram support natural language questions through the same RAG pipeline:
- Just type your question — no slash commands needed
- Follow-up questions work naturally (conversation memory, 60-min sessions)
- Type "clear" to reset your conversation
- **Discord:** use `#ask-gsa`, DM the bot, or @mention it in any channel
- **Telegram:** DM @njit_gsa_bot directly

---

## Running the Bots

```bash
# Both bots together (development)
bash scripts/run_bot.sh

# Restart both
bash scripts/restart.sh

# Health check
bash scripts/health_check.sh
bash scripts/health_check.sh --fix   # auto-restart if down
```

For production (systemd):
```bash
sudo systemctl start gsa-gateway gsa-telegram
sudo systemctl restart gsa-gateway gsa-telegram
```

---

## Project Structure

```
gsa-gateway/
├── bot/
│   ├── commands/        One file per slash command (ask, events, admin, etc.)
│   ├── connectors/      Platform connectors: base.py, telegram_connector.py
│   ├── core/            Platform-agnostic brain: message_handler.py
│   ├── services/        Database, search, KB, Ollama, scheduler, channels, announcements
│   └── data/            Edit these YAML/Markdown files to update content
├── run_telegram.py      Telegram bot entry point (runs independently)
├── website/             Static site — deploy with one command
├── scripts/
│   ├── health_check.sh         Check all services + auto-restart (--fix flag)
│   ├── restart.sh              Restart both bots
│   ├── build_index.py          Rebuild ChromaDB vector index after KB edits
│   ├── export_events_json.py   Sync events.yml → website/data/events.json
│   ├── gsa-gateway.service     systemd unit — Discord bot
│   └── gsa-telegram.service    systemd unit — Telegram bot
└── docs/                Architecture, deployment, admin guide, privacy policy
```

---

## Privacy

User IDs are SHA-256 hashed before any database write. Raw Discord and Telegram IDs are never stored. See [docs/privacy_policy.md](docs/privacy_policy.md).

---

## License

MIT © 2026 NJIT Graduate Student Association.
Built by Mohammad Dindoost, VP Academic Affairs.

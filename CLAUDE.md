# GSA Gateway — Claude Code Session Guide

## Project Summary
Discord bot + static website for NJIT's Graduate Student Association (GSA).
- **Bot**: Python 3.11+, discord.py 2.x, SQLite, rapidfuzz, optional Ollama
- **Website**: Pure HTML/CSS/JS, GitHub Pages compatible
- **Maintainer**: Mohammad Dindoost (VP Academic Affairs)
- **Running on**: Always-on local machine; planned migration to NJIT server

---

## File Map

```
gsa-gateway/
├── bot/
│   ├── main.py              Entry point — GSABot class, loads cogs, syncs slash commands
│   ├── config.py            Reads .env → typed Config dataclass (singleton: `config`)
│   ├── commands/
│   │   ├── ask.py           /ask — fuzzy-searches knowledge base, optional Ollama
│   │   ├── events.py        /events (list) and /event [name] (detail)
│   │   ├── initiative.py    /initiative — Discord Modal with 5 fields → SQLite
│   │   ├── feedback.py      /feedback — stores anonymous message → SQLite
│   │   ├── resources.py     /resources [category] — lists YAML resources
│   │   ├── contact.py       /contact [role] — directory lookup from contacts.yml
│   │   ├── help_cmd.py      /help — ephemeral embed with command reference
│   │   └── admin.py         /admin_summary /admin_export /admin_stats
│   │                        /admin_announce /admin_add_event  (all ephemeral)
│   ├── services/
│   │   ├── database.py      Database class — all SQLite CRUD + hash_user_id()
│   │   ├── knowledge_base.py KnowledgeBase dataclass — loads MD + YAML files
│   │   ├── search.py        SearchService — rapidfuzz fuzzy search over KB
│   │   ├── moderation.py    RateLimiter, is_channel_allowed(), is_admin()
│   │   ├── ollama_client.py OllamaClient — optional local LLM (never hallucinates)
│   │   └── summaries.py     SummaryService.weekly_summary() → markdown text
│   ├── data/
│   │   ├── gsa_faq.md       12+ Q&A pairs; format: ## Q: ...\n**A:** ...
│   │   ├── events.yml       5 events with full details
│   │   ├── contacts.yml     9 contacts (GSA officers + key NJIT offices)
│   │   ├── resources.yml    8 categories × 3–4 resources each
│   │   └── rules.md         Community guidelines (not searched, for reference)
│   └── tests/
│       ├── conftest.py      Fixtures: db (in-memory SQLite), kb, search_svc
│       ├── test_search.py   Fuzzy search hits, misses, confidence thresholds
│       ├── test_database.py CRUD tests, privacy hashing, stats
│       └── test_commands.py Admin role check, rate limiter, channel allowlist
├── website/                 Static site — GitHub Pages ready
│   ├── index.html           Hero, features, command table
│   ├── about.html           GSA mission, officer bios
│   ├── events.html          Loads website/data/events.json via fetch()
│   ├── initiatives.html     CTA to use /initiative in Discord
│   ├── resources.html       Static resource listings
│   ├── contact.html         Officer and campus office directory
│   ├── assets/
│   │   ├── style.css        NJIT red #CC0000 + dark gray; mobile-first; no frameworks
│   │   └── app.js           Nav toggle, events loader, HTML escaping
│   └── data/
│       └── events.json      Auto-exported by scripts/export_events_json.py
├── scripts/
│   ├── init_db.py           Creates all SQLite tables (safe to re-run)
│   ├── export_events_json.py Syncs events.yml → website/data/events.json
│   ├── export_weekly_summary.py Prints or saves the 7-day admin summary
│   ├── setup.sh             Full first-time setup (venv, deps, DB, tests)
│   └── run_bot.sh           Starts the bot with log file
└── docs/
    ├── architecture.md
    ├── deployment.md        Includes NJIT server migration section
    ├── admin_guide.md
    ├── privacy_policy.md
    └── student_usage_guide.md
```

---

## Shared Bot State

Services are attached to the bot instance in `setup_hook()`:

| Attribute | Type | Description |
|---|---|---|
| `bot.db` | `Database` | SQLite CRUD operations |
| `bot.kb` | `KnowledgeBase` | Loaded data files |
| `bot.search_svc` | `SearchService` | Fuzzy search |
| `bot.rate_limiter` | `RateLimiter` | In-memory per-user throttle |
| `bot.ollama` | `OllamaClient` | Optional; only set if `OLLAMA_ENABLED=true` |

All cogs access these via `self.bot.db`, etc.

---

## Common Tasks

### Add a new slash command
1. Create `bot/commands/mycommand.py` following the cog pattern (see `feedback.py` for minimal example).
2. Add `"bot.commands.mycommand"` to `EXTENSIONS` in `bot/main.py`.
3. That's it — the command syncs on next restart.

### Add new FAQ entries
Edit `bot/data/gsa_faq.md`. Format strictly:
```markdown
## Q: Your question here?
**A:** Your answer here (can span multiple lines).
```
Restart the bot (or call `bot.kb.load()` if you add a hot-reload command later).

### Add a new event
Edit `bot/data/events.yml` — copy an existing block. Then:
```bash
python scripts/export_events_json.py   # updates website
```
Restart the bot.

### Add a new resource category
Edit `bot/data/resources.yml` — add a new top-level key under `resources:`.

### Add a new contact
Edit `bot/data/contacts.yml` — add a new key under `contacts:` following the existing schema.

### Enable Ollama
Set in `.env`:
```
OLLAMA_ENABLED=true
OLLAMA_MODEL=llama3
OLLAMA_BASE_URL=http://localhost:11434
```
Pull the model first: `ollama pull llama3`

### Add a second admin officer
Just assign the `ADMIN_ROLE_NAME` Discord role to them — no code change needed.

---

## Key Invariants

- **User IDs are never stored raw.** Always use `hash_user_id(user_id)` before DB writes.
- **Ollama never answers without KB context.** The prompt always prepends retrieved FAQ chunks.
- **Admin commands must be ephemeral.** All responses in `admin.py` use `ephemeral=True`.
- **Rate limiter is in-memory.** It resets on bot restart — this is intentional for simplicity.
- **Channel allowlist** in `ALLOWED_CHANNELS` is comma-separated channel *names* (not IDs).
- **Confidence threshold** for search is 60.0. Below that, the fallback message is shown.

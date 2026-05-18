# GSA Gateway

**An AI-assisted Discord bot + static website for NJIT's Graduate Student Association.**

> GSA Gateway makes GSA information, events, resources, and student initiatives more accessible to all NJIT graduate students.

<!-- Screenshot placeholder — add `docs/screenshot_bot.png` and `docs/screenshot_web.png` -->
<!-- ![Bot screenshot](docs/screenshot_bot.png) -->
<!-- ![Website screenshot](docs/screenshot_web.png) -->

---

## Features

| Area | Details |
|---|---|
| **Knowledge Base** | `/ask` — fuzzy-search 12+ GSA FAQ entries, optional Ollama enhancement |
| **Events** | `/events` and `/event [name]` — load from `events.yml`, sorted by date |
| **Initiatives** | `/initiative` — Discord Modal form, anonymous by default, stored in SQLite |
| **Feedback** | `/feedback` — private anonymous messages to officers |
| **Resources** | `/resources [category]` — 8 categories, 28+ curated links |
| **Directory** | `/contact [role]` — GSA officers + key NJIT campus offices |
| **Admin Tools** | `/admin_summary`, `/admin_export`, `/admin_stats`, `/admin_announce` (all ephemeral) |
| **Privacy** | SHA-256 hashed user IDs — raw IDs never stored |
| **Rate Limiting** | 5 commands per user per 60 seconds |
| **Channel Allowlist** | Restrict bot to specific channels via `ALLOWED_CHANNELS` env var |
| **Website** | GitHub Pages-ready, loads events from `events.json`, mobile-responsive |

---

## Installation

### 1. Prerequisites
- Python 3.11+
- Git

### 2. Clone and set up
```bash
git clone https://github.com/YOUR_USERNAME/gsa-gateway.git
cd gsa-gateway
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Create your Discord bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → name it "GSA Gateway"
3. Go to **Bot** → click **Add Bot** → copy the **Token**
4. Under **Privileged Gateway Intents**, enable:
   - `Message Content Intent`
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Use Application Commands`
6. Copy the generated URL, open it, and invite the bot to your Discord server
7. In your Discord server, copy the **Server ID** (right-click server → Copy Server ID — requires Developer Mode)

### 4. Configure .env
```bash
cp .env.example .env
```
Edit `.env`:
```
DISCORD_TOKEN=your_bot_token_here
DISCORD_GUILD_ID=your_server_id_here
ADMIN_ROLE_NAME=GSA Officer
DATABASE_PATH=./gsa_gateway.db
OLLAMA_ENABLED=false
LOG_LEVEL=INFO
ALLOWED_CHANNELS=
```

### 5. Initialise the database
```bash
python scripts/init_db.py
```

### 6. Export events to website
```bash
python scripts/export_events_json.py
```

### 7. Run the bot
```bash
bash scripts/run_bot.sh
```
Or directly: `python -m bot.main`

---

## Running Tests
```bash
pytest bot/tests/ -v
```

---

## Updating Content (No Coding Required)

### Add or edit an FAQ entry
Edit `bot/data/gsa_faq.md`. Format:
```markdown
## Q: Your question here?
**A:** Your answer here.
```
Restart the bot.

### Add an event
Edit `bot/data/events.yml` — copy an existing block:
```yaml
- name: "New Event Name"
  date: YYYY-MM-DD
  time: "6:00 PM – 8:00 PM"
  location: "Building, Room"
  description: "Description text."
  organizer: "Committee or officer name"
  rsvp_link: "https://..."
  category: networking
```
Then: `python scripts/export_events_json.py` + restart the bot.

### Update contacts or resources
Edit `bot/data/contacts.yml` or `bot/data/resources.yml`. Restart the bot.

---

## Deploy Website on GitHub Pages

1. Push `website/` to your GitHub repo
2. Go to **Settings → Pages** → Source: `main` branch, `/website` folder
3. Your site will be live at `https://USERNAME.github.io/gsa-gateway/`
4. To update events on the website: `python scripts/export_events_json.py` → commit and push `website/data/events.json`

---

## Enable Ollama (Optional Local LLM)

1. Install Ollama: [ollama.com](https://ollama.com)
2. Pull a model: `ollama pull llama3`
3. Set in `.env`: `OLLAMA_ENABLED=true` and `OLLAMA_MODEL=llama3`
4. Restart the bot

The bot will use Ollama to generate enhanced answers, but **only with retrieved knowledge base context** — it never answers without grounding. Falls back silently if Ollama is unavailable.

---

## Using Admin Commands

Assign the `GSA Officer` Discord role (or whatever you set in `ADMIN_ROLE_NAME`) to officers. Then:

- `/admin_summary` — 7-day summary of initiatives + feedback
- `/admin_export initiatives` — download CSV of all submissions
- `/admin_stats` — total counts and top search topics
- `/admin_announce #channel Your message` — post an announcement embed
- `/admin_add_event` — instructions for adding events

All admin commands are **ephemeral** — only visible to the person who ran them.

---

## NJIT Server Migration

See [docs/deployment.md](docs/deployment.md) for the full migration checklist.
Short version: copy files + `.env` + `gsa_gateway.db` to the server, set up a venv, and configure systemd.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `DISCORD_TOKEN is not set` | Check `.env` — token must not have quotes around it |
| Commands not appearing | Make sure `DISCORD_GUILD_ID` is set; global sync takes up to 1 hour |
| `ModuleNotFoundError` | Run from the project root; activate the venv |
| Bot offline but no error | Check `gsa_gateway.log` for the root cause |
| Ollama not responding | Confirm `ollama serve` is running; set `OLLAMA_ENABLED=false` to skip |
| Rate limit fires too fast | Adjust `max_calls` and `period_seconds` in `bot/main.py` `setup_hook` |
| Admin commands not working | Ensure the user's Discord role name matches `ADMIN_ROLE_NAME` exactly (case-sensitive) |

---

## License

MIT © 2026 NJIT Graduate Student Association. See [LICENSE](LICENSE).

Built by Mohammad Dindoost, VP Academic Affairs.

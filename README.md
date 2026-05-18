# GSA Gateway

**Discord bot + static website for NJIT's Graduate Student Association.**

> GSA Gateway is an AI-assisted student communication initiative designed to make GSA information, events, resources, and student ideas more accessible to all NJIT graduate students.

**Live website:** https://mdindoost.github.io/gsa-gateway/
**GitHub repo:** https://github.com/mdindoost/gsa-gateway

---

## Features

| Area | Details |
|---|---|
| **Knowledge Base** | `/ask` — fuzzy-search 14 GSA FAQ entries, optional Ollama enhancement |
| **Events** | `/events` and `/event [name]` — loaded from `events.yml`, sorted by date |
| **Initiatives** | `/initiative` — Discord Modal form, anonymous by default, stored in SQLite |
| **Feedback** | `/feedback` — private anonymous messages to officers |
| **Resources** | `/resources [category]` — 8 categories, 28+ curated links |
| **Directory** | `/contact [role]` — GSA officers + key NJIT campus offices |
| **Admin Tools** | `/admin_summary`, `/admin_export`, `/admin_stats`, `/admin_announce` (all ephemeral) |
| **Privacy** | SHA-256 hashed user IDs — raw IDs never stored |
| **Rate Limiting** | 5 commands per user per 60 seconds |
| **Website** | Live on GitHub Pages, loads events from `events.json`, mobile-responsive |

---

## First-Time Setup

### 1. Clone and create virtual environment
```bash
git clone git@github.com:mdindoost/gsa-gateway.git
cd gsa-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
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

### 3. Create Discord bot (one-time)
1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. **New Application** → name it "GSA Gateway"
3. **Bot** → **Add Bot** → copy the **Token** → paste as `DISCORD_TOKEN` in `.env`
4. Enable **Message Content Intent** under Privileged Gateway Intents → Save
5. **OAuth2 → URL Generator** → Scopes: `bot`, `applications.commands` → Permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Use Application Commands`
6. Open the generated URL → invite the bot to your server
7. In Discord: **Settings → Advanced → Developer Mode ON** → right-click server name → **Copy Server ID** → paste as `DISCORD_GUILD_ID` in `.env`

### 4. Initialise the database
```bash
python scripts/init_db.py
```

### 5. Run the bot
```bash
source .venv/bin/activate
python -m bot.main
```
You should see `GSA Gateway ready — logged in as GSA Gateway#XXXX`.
Test in Discord with `/help`.

---

## Daily: Start the Bot
```bash
cd ~/gsa-gateway
source .venv/bin/activate
python -m bot.main
```

### Keep it running permanently (systemd)
Create `/etc/systemd/system/gsa-gateway.service`:
```ini
[Unit]
Description=GSA Gateway Discord Bot
After=network.target

[Service]
Type=simple
User=md724
WorkingDirectory=/home/md724/gsa-gateway
ExecStart=/home/md724/gsa-gateway/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now gsa-gateway
```

---

## Updating Content (No Coding Required)

### Add or edit a FAQ entry
1. Edit `bot/data/gsa_faq.md`
2. Use this exact format:
```markdown
## Q: Your question here?
**A:** Your answer here.
```
3. Restart the bot

### Add an event
1. Edit `bot/data/events.yml` — copy an existing block and paste at the bottom:
```yaml
  - name: "Event Name"
    date: 2026-09-01
    time: "6:00 PM - 8:00 PM"
    location: "Building, Room, NJIT"
    description: "Description here."
    organizer: "Your name or committee"
    rsvp_link: "https://njit.campuslabs.com/engage/organization/gsa"
    category: "social"
```
> Use **spaces not tabs**. Categories: `academic`, `social`, `career`, `networking`, `international`, `wellness`, `other`

2. Sync, commit, and deploy:
```bash
source .venv/bin/activate
python scripts/export_events_json.py
git add bot/data/events.yml website/data/events.json
git commit -m "Add event: Event Name"
git push origin main
bash scripts/deploy_website.sh
```
3. Restart the bot

### Update officer contacts
1. Edit `bot/data/contacts.yml` (used by the Discord bot `/contact` command)
2. Edit `website/contact.html` (the website is static — update it manually)
3. Commit and deploy:
```bash
git add bot/data/contacts.yml website/contact.html
git commit -m "Update officer contacts"
git push origin main
bash scripts/deploy_website.sh
```
4. Restart the bot

### Update resources
1. Edit `bot/data/resources.yml`
2. Restart the bot
3. The website resources page (`website/resources.html`) is static — edit it manually if needed

---

## Deploy Website to GitHub Pages

Live site: **https://mdindoost.github.io/gsa-gateway/**

After any change to files in `website/`:
```bash
bash scripts/deploy_website.sh
```
GitHub Pages updates within ~1 minute.

---

## Running Tests
```bash
source .venv/bin/activate
pytest bot/tests/ -v
```
Expected: 55 tests pass.

---

## Admin Discord Commands

Assign the `GSA Officer` role in Discord to officers. All commands are **ephemeral** (only the officer can see the response).

| Command | What it does |
|---|---|
| `/admin_summary` | 7-day summary of initiatives + feedback |
| `/admin_export initiatives` | Download CSV of all initiative submissions |
| `/admin_export feedback` | Download CSV of all feedback |
| `/admin_export questions` | Download CSV of all questions asked |
| `/admin_stats` | Total counts and top search topics |
| `/admin_announce #channel message` | Post an announcement embed to a channel |
| `/admin_add_event` | Instructions for adding events |

---

## Enable Ollama (Optional Local LLM)
1. Install Ollama: [ollama.com](https://ollama.com)
2. `ollama pull llama3`
3. Set in `.env`: `OLLAMA_ENABLED=true`, `OLLAMA_MODEL=llama3`
4. Restart the bot

---

## Export Weekly Summary
```bash
source .venv/bin/activate
python scripts/export_weekly_summary.py           # print to terminal
python scripts/export_weekly_summary.py --save    # save to exports/
python scripts/export_weekly_summary.py --days 14 # 2-week window
```

---

## NJIT Server Migration
Full guide: [docs/deployment.md](docs/deployment.md)

```bash
git clone git@github.com:mdindoost/gsa-gateway.git
cd gsa-gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Copy .env and gsa_gateway.db from old machine via scp
python scripts/init_db.py
python -m bot.main
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `DISCORD_TOKEN is not set` | Check `.env` — no quotes around the token value |
| Commands not showing in Discord | Ensure `DISCORD_GUILD_ID` is set; global sync takes up to 1 hour |
| `/ask` always returns fallback | Restart the bot — knowledge base loads at startup |
| `ModuleNotFoundError` | Activate venv and run from project root |
| Bot offline, no error | Check `gsa_gateway.log` |
| Ollama not responding | Run `ollama serve`; or set `OLLAMA_ENABLED=false` |
| Admin commands denied | Role name must match `ADMIN_ROLE_NAME` in `.env` exactly (case-sensitive) |
| Website not updating | Run `bash scripts/deploy_website.sh` and wait ~1 min |
| YAML turns red in editor | Use spaces not tabs; 2 spaces before `-`, 4 spaces before field names |

---

## Project Structure

```
gsa-gateway/
├── bot/
│   ├── main.py              Bot entry point
│   ├── config.py            Loads .env into typed Config object
│   ├── commands/            One file per slash command
│   ├── services/            Database, search, knowledge base, moderation
│   ├── data/                YAML + Markdown files — edit these to update content
│   └── tests/               55 pytest tests
├── website/                 Static site (GitHub Pages)
│   ├── *.html               Edit directly to update website pages
│   ├── assets/              style.css and app.js
│   └── data/events.json     Auto-generated — do not edit manually
├── scripts/
│   ├── deploy_website.sh    ONE command to push website live
│   ├── export_events_json.py Sync events.yml to website/data/events.json
│   ├── export_weekly_summary.py
│   ├── init_db.py
│   └── run_bot.sh
└── docs/                    Architecture, deployment, admin guide, privacy policy
```

---

## License

MIT © 2026 NJIT Graduate Student Association.
Built by Mohammad Dindoost, VP Academic Affairs.

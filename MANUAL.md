# GSA Gateway — Operations Manual

Everything you need to run, maintain, and extend the GSA Gateway project.
Written for Mohammad Dindoost (VP Academic Affairs) and anyone who takes over the project.

---

## Quick Reference

```bash
# Stop everything
sudo systemctl stop gsa-gateway
sudo systemctl stop ollama

# Start everything
sudo systemctl start ollama
sudo systemctl start gsa-gateway

# Restart bot after any change
sudo systemctl restart gsa-gateway

# Watch live logs
journalctl -u gsa-gateway -f

# Check Ollama is alive
curl http://localhost:11434

# Run tests
source .venv/bin/activate && pytest bot/tests/ -v

# Deploy website
bash scripts/deploy_website.sh
```

---

## What Is Running on This Machine

| Service | How it runs | Config |
|---|---|---|
| GSA Gateway bot | systemd: `gsa-gateway` | `/home/md724/gsa-gateway/.env` |
| Ollama (local AI) | systemd: `ollama` | `/home/md724/gsa-gateway/.env` |
| Website | GitHub Pages (static) | pushed via `scripts/deploy_website.sh` |
| Database | SQLite file | `./gsa_gateway.db` |

The bot runs 24/7 under systemd and auto-restarts on crash or reboot.
Ollama provides AI-powered answers to `/ask` and AI summaries for `/admin_summary`.

---

## Starting and Stopping

### Bot

```bash
sudo systemctl start gsa-gateway      # start
sudo systemctl stop gsa-gateway       # stop
sudo systemctl restart gsa-gateway    # restart (after any code or .env change)
sudo systemctl status gsa-gateway     # check if running
```

Expected output when healthy:
```
Active: active (running)
```
And in the logs:
```
Ollama is reachable at http://localhost:11434 (model: llama3)
GSA Gateway ready — logged in as GSA Gateway#0699
```

### Ollama

```bash
sudo systemctl start ollama
sudo systemctl stop ollama
sudo systemctl status ollama
curl http://localhost:11434            # should print: Ollama is running
```

**Always start Ollama before the bot.** If Ollama is off when the bot starts, the bot still runs but falls back to plain FAQ text for `/ask`.

### Run bot manually (without systemd, for testing)

```bash
cd /home/md724/gsa-gateway
source .venv/bin/activate
python -m bot.main
```

Press `Ctrl+C` to stop.

---

## Logs

```bash
journalctl -u gsa-gateway -f              # live log stream
journalctl -u gsa-gateway -n 50          # last 50 lines
journalctl -u gsa-gateway --no-pager | grep -i ollama   # check Ollama init
journalctl -u gsa-gateway --no-pager | grep -i error    # check for errors
```

There is also a file log at `/home/md724/gsa-gateway/gsa_gateway.log`.

---

## Environment Variables (.env)

File location: `/home/md724/gsa-gateway/.env`

After editing `.env`, always restart the bot:
```bash
sudo systemctl restart gsa-gateway
```

| Variable | Current value | What it does |
|---|---|---|
| `DISCORD_TOKEN` | (your bot token) | Bot authentication — never share this |
| `DISCORD_GUILD_ID` | `1505973021943267358` | Your Discord server ID — syncs commands there instantly |
| `ADMIN_ROLE_NAME` | `GSA Officer` | Discord role name that unlocks `/admin_*` commands — case-sensitive |
| `DATABASE_PATH` | `./gsa_gateway.db` | SQLite database file path |
| `OLLAMA_ENABLED` | `true` | `true` = AI answers on, `false` = plain FAQ text |
| `OLLAMA_MODEL` | `llama3` | Which AI model to use |
| `OLLAMA_URL` | `http://localhost:11434` | Where Ollama is running |
| `OLLAMA_TIMEOUT` | `90` | Seconds to wait for AI response before falling back |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose, `INFO` for normal, `WARNING` for quiet |
| `ALLOWED_CHANNELS` | (empty) | Comma-separated channel names. Empty = bot responds in all channels |
| `BOT_PREFIX` | `gsa` | Legacy text prefix — not used for slash commands |

---

## Updating Content (No Coding Required)

### Add or edit a FAQ entry

1. Edit `bot/data/gsa_faq.md`
2. Use this exact format — every entry must look like this:
```markdown
## Q: Your question here?
**A:** Your answer here. Can span multiple lines.
```
3. Restart the bot: `sudo systemctl restart gsa-gateway`

The bot uses fuzzy search so students don't need to type exact questions. Aim for natural question phrasing.

---

### Add or edit an event

1. Edit `bot/data/events.yml` — copy an existing block:
```yaml
  - name: "Event Name"
    date: 2026-09-01
    time: "6:00 PM – 8:00 PM"
    location: "Building, Room, NJIT"
    description: "Description here."
    organizer: "Your name or committee"
    rsvp_link: "https://njit.campuslabs.com/engage/organization/gsa"
    category: "social"
```
Categories: `academic`, `social`, `career`, `networking`, `international`, `wellness`, `other`

> Use spaces, not tabs. 2 spaces before `-`, 4 spaces before field names. If lines turn red in your editor, it's a tab/indent issue.

2. Sync to the website and push:
```bash
source .venv/bin/activate
python scripts/export_events_json.py
git add bot/data/events.yml website/data/events.json
git commit -m "Add event: Event Name"
git push origin main
bash scripts/deploy_website.sh
```

3. Restart the bot: `sudo systemctl restart gsa-gateway`

---

### Update officer contacts

Two places to update:

**Bot** (Discord `/contact` command):
```
bot/data/contacts.yml
```

**Website** (static HTML — must edit manually):
```
website/contact.html
```

After editing both:
```bash
git add bot/data/contacts.yml website/contact.html
git commit -m "Update officer contacts"
git push origin main
bash scripts/deploy_website.sh
sudo systemctl restart gsa-gateway
```

---

### Update resources

1. Edit `bot/data/resources.yml` — add a link under an existing category or add a new category key
2. Restart the bot: `sudo systemctl restart gsa-gateway`
3. If you want the website resources page updated too, edit `website/resources.html` manually (it is static)

---

## Website

**Live at:** https://mdindoost.github.io/gsa-gateway/

The website is pure HTML/CSS/JS — no build step. After any change to files in `website/`:
```bash
bash scripts/deploy_website.sh
```
GitHub Pages updates within ~1 minute.

The events page (`website/events.html`) fetches `website/data/events.json` at runtime. That file is auto-generated — never edit it by hand. Always run `python scripts/export_events_json.py` to regenerate it from `events.yml`.

---

## Ollama AI

### What it does

When `OLLAMA_ENABLED=true`, the bot:
- Passes the student's question + top 3 FAQ matches to llama3
- Returns a natural-language answer (instead of raw FAQ text) for `/ask`
- Generates a themed summary for `/admin_summary` that groups submissions and suggests officer action items

The AI **only** summarises what is already in the knowledge base. It cannot invent information.
If Ollama is unavailable, the bot falls back silently — no crash, no error shown to students.

### Enable or disable

```bash
# Edit .env
OLLAMA_ENABLED=true    # on
OLLAMA_ENABLED=false   # off

# Then restart
sudo systemctl restart gsa-gateway
```

### Switch models

```bash
ollama pull llama3.2:1b           # pull the faster lightweight model
# Edit .env: OLLAMA_MODEL=llama3.2:1b
sudo systemctl restart gsa-gateway
```

| Model | Size | Speed | Quality |
|---|---|---|---|
| `llama3` | 4.7 GB | ~30–60s/response | Good |
| `llama3.2:1b` | 1.3 GB | ~5–10s/response | Shorter answers |
| `mistral` | 4.1 GB | ~20–40s/response | Good alternative |

### Check Ollama is working

```bash
curl http://localhost:11434                     # prints: Ollama is running
ollama list                                     # shows downloaded models
journalctl -u gsa-gateway --no-pager | grep -i ollama  # shows init messages
```

---

## Discord Admin Commands

Assign the `GSA Officer` role in Discord to any officer. All commands are ephemeral (only the officer who runs them can see the response).

| Command | What it does |
|---|---|
| `/admin_summary` | AI-generated themed summary of last 7 days of initiatives and feedback (falls back to plain list if Ollama is off) |
| `/admin_export initiatives` | Download CSV of all initiative submissions |
| `/admin_export feedback` | Download CSV of all feedback messages |
| `/admin_export questions` | Download CSV of all questions asked |
| `/admin_stats` | Total counts and top search topics |
| `/admin_announce #channel message` | Post a GSA announcement embed to any channel |
| `/admin_add_event` | Shows step-by-step event-adding instructions |

### Assign the admin role

Discord server settings → Roles → find or create a role named exactly `GSA Officer` (matches `ADMIN_ROLE_NAME` in `.env`) → assign to officers.

---

## Export Weekly Summary (Terminal)

Run this on the server directly — does not require Discord:

```bash
source .venv/bin/activate
python scripts/export_weekly_summary.py            # print to terminal
python scripts/export_weekly_summary.py --save     # save to exports/ folder
python scripts/export_weekly_summary.py --days 14  # 2-week window
```

---

## Running Tests

```bash
source .venv/bin/activate
pytest bot/tests/ -v
```

Expected: **65 tests pass** (55 core + 10 Ollama tests). Runtime: ~0.2 seconds.

Run this any time you change bot code to catch regressions before restarting the live service.

---

## First-Time Setup on a New Machine

Use this if you migrate to a new server (e.g. NJIT Linux server).

```bash
# 1. Clone the repo
git clone git@github.com:mdindoost/gsa-gateway.git
cd gsa-gateway

# 2. Create Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Copy your .env and database from the old machine
scp oldserver:/home/md724/gsa-gateway/.env .
scp oldserver:/home/md724/gsa-gateway/gsa_gateway.db .

# 4. Initialise database tables (safe to re-run)
python scripts/init_db.py

# 5. Install systemd service
sudo cp scripts/gsa-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gsa-gateway

# 6. Install Ollama (optional)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
```

---

## Adding a New Slash Command (Developer Guide)

1. Create `bot/commands/mycommand.py` — follow this pattern:
```python
import discord
from discord import app_commands
from discord.ext import commands

class MyCog(commands.Cog, name="MyCommand"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="mycommand", description="Does something.")
    async def mycommand(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello!")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyCog(bot))
```

2. Register it in `bot/main.py` — add to the `EXTENSIONS` list:
```python
EXTENSIONS = [
    ...
    "bot.commands.mycommand",   # add this line
]
```

3. Restart the bot. The command syncs to Discord automatically.

Available services inside any cog:
- `self.bot.db` — database (SQLite CRUD)
- `self.bot.kb` — knowledge base (loaded FAQ, events, etc.)
- `self.bot.search_svc` — fuzzy search
- `self.bot.rate_limiter` — per-user rate limiter
- `self.bot.ollama` — Ollama client (check `config.ollama_enabled` first)

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `DISCORD_TOKEN is not set` | Check `.env` — no quotes around the token value |
| Commands not showing in Discord | Ensure `DISCORD_GUILD_ID` is set; restart the bot |
| `/ask` always returns fallback | Restart the bot — knowledge base loads at startup |
| `ModuleNotFoundError` | Run from project root with venv activated |
| Bot offline, no logs | Run `journalctl -u gsa-gateway -n 50` |
| Ollama not responding | Run `sudo systemctl start ollama`; or set `OLLAMA_ENABLED=false` |
| `/ask` is very slow | Ollama is thinking — normal for llama3 (30–60s). Switch to `llama3.2:1b` for speed |
| Admin commands denied | Role name must match `ADMIN_ROLE_NAME` in `.env` exactly — case-sensitive |
| Website not updating | Run `bash scripts/deploy_website.sh` and wait ~1 min |
| YAML turns red in editor | Use spaces not tabs; 2 spaces before `-`, 4 spaces before field names |
| Database is locked | Another process has `gsa_gateway.db` open — restart the bot |

---

## Project File Map

```
gsa-gateway/
├── MANUAL.md                 ← You are here
├── README.md                 Project overview
├── .env                      Your secrets and settings — never commit this
├── .env.example              Template for .env — safe to commit
├── requirements.txt          Python dependencies
├── gsa_gateway.db            SQLite database (auto-created)
│
├── bot/
│   ├── main.py               Entry point — GSABot class, loads all cogs
│   ├── config.py             Reads .env into a typed Config object
│   ├── commands/
│   │   ├── ask.py            /ask — search + Ollama AI answer
│   │   ├── events.py         /events and /event [name]
│   │   ├── initiative.py     /initiative — modal form → SQLite
│   │   ├── feedback.py       /feedback — anonymous → SQLite
│   │   ├── resources.py      /resources [category]
│   │   ├── contact.py        /contact [role]
│   │   ├── help_cmd.py       /help
│   │   └── admin.py          /admin_summary /admin_export /admin_stats
│   │                         /admin_announce /admin_add_event
│   ├── services/
│   │   ├── database.py       All SQLite operations; SHA-256 user ID hashing
│   │   ├── knowledge_base.py Loads gsa_faq.md, events.yml, etc. at startup
│   │   ├── search.py         Fuzzy search via rapidfuzz (60% threshold)
│   │   ├── moderation.py     Rate limiter, channel allowlist, admin check
│   │   ├── ollama_client.py  Ollama HTTP wrapper — generate_answer(), check_connection()
│   │   └── summaries.py      weekly_summary() and generate_ai_summary() for /admin_summary
│   ├── data/
│   │   ├── gsa_faq.md        Edit to add/update FAQ entries ← edit this
│   │   ├── events.yml        Edit to add/update events ← edit this
│   │   ├── contacts.yml      Edit to update officer info ← edit this
│   │   ├── resources.yml     Edit to add/update resource links ← edit this
│   │   └── rules.md          Community guidelines (reference only)
│   └── tests/
│       ├── conftest.py       Shared fixtures (in-memory DB, sample KB)
│       ├── test_search.py    Fuzzy search tests
│       ├── test_database.py  SQLite CRUD and privacy tests
│       ├── test_commands.py  Rate limiter, admin role, channel allowlist
│       └── test_ollama.py    Ollama client tests (all mocked)
│
├── website/                  Static site — GitHub Pages
│   ├── index.html            Home page
│   ├── about.html            GSA mission and officers
│   ├── events.html           Loads events.json at runtime
│   ├── initiatives.html      CTA to use /initiative
│   ├── resources.html        Static resource listings
│   ├── contact.html          Officer and campus directory
│   ├── assets/
│   │   ├── style.css         NJIT red #CC0000, mobile-first, no frameworks
│   │   └── app.js            Nav, events loader, HTML escaping
│   └── data/
│       └── events.json       Auto-generated — never edit manually
│
├── scripts/
│   ├── deploy_website.sh     Push website/ to gh-pages branch on GitHub
│   ├── export_events_json.py Sync events.yml → website/data/events.json
│   ├── export_weekly_summary.py  Print or save the 7-day summary
│   ├── init_db.py            Create SQLite tables (safe to re-run)
│   ├── gsa-gateway.service   systemd unit file (copy to /etc/systemd/system/)
│   └── run_bot.sh            Manual start script (alternative to systemd)
│
└── docs/
    ├── architecture.md       How the pieces fit together
    ├── deployment.md         Server migration full guide
    ├── admin_guide.md        Discord admin command reference
    ├── privacy_policy.md     Data handling and SHA-256 hashing policy
    └── student_usage_guide.md  Guide for students using the bot
```

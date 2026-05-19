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
sudo journalctl -u gsa-gateway -f

# Check Ollama is alive
curl http://localhost:11434

# Run tests
source .venv/bin/activate && pytest bot/tests/ -v

# Deploy website
bash scripts/deploy_website.sh

# Regenerate events.json from YAML (then deploy)
source .venv/bin/activate
python scripts/export_events_json.py
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
The scheduler (built into the bot) fires reminders and a daily digest automatically.

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
GSA Gateway ready — logged in as GSA Gateway#XXXX
Knowledge base active: 30 FAQ entries, 13 contacts, 8 events, 7 resource categories
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
sudo journalctl -u gsa-gateway -f              # live log stream
sudo journalctl -u gsa-gateway -n 50          # last 50 lines
sudo journalctl -u gsa-gateway --no-pager | grep -i error    # check for errors
sudo journalctl -u gsa-gateway --no-pager | grep -i scheduler  # reminder/digest activity
```

There is also a file log at `/home/md724/gsa-gateway/gsa_gateway.log`.

---

## Environment Variables (.env)

File location: `/home/md724/gsa-gateway/.env`

After editing `.env`, always restart the bot:
```bash
sudo systemctl restart gsa-gateway
```

### Core settings

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

### Announcement channel settings

These must match your Discord channel names **exactly** (lowercase, dashes).

| Variable | Default | What it does |
|---|---|---|
| `CHANNEL_ANNOUNCEMENTS` | `gsa-announcements` | Receives all event announcements — the main fallback |
| `CHANNEL_EVENTS` | `gsa-events` | Academic, social, and general events |
| `CHANNEL_FOOD` | `gsa-food` | Food events and happy hours |
| `CHANNEL_FUNDING` | `gsa-funding` | Grants, awards, financial aid events |
| `CHANNEL_WELLNESS` | `gsa-wellness` | Mental health and wellness programs |
| `CHANNEL_RESEARCH` | `gsa-research` | Research seminars, workshops |
| `CHANNEL_INTERNATIONAL` | `gsa-international` | International student events |

If a category channel doesn't exist in Discord, the bot automatically falls back to `CHANNEL_ANNOUNCEMENTS`. Nothing crashes.

### Scheduler settings

| Variable | Default | What it does |
|---|---|---|
| `DAILY_DIGEST_HOUR` | `9` | UTC hour for the morning digest post (0–23) |
| `DAILY_DIGEST_MINUTE` | `0` | Minute within that hour |
| `REMINDER_CHECK_INTERVAL` | `30` | How often (minutes) to check for upcoming event reminders |

---

## Announcement Channels Setup (Discord)

Create these text channels in your Discord server. Names must match the `.env` values above.

| Channel | Purpose |
|---|---|
| `gsa-announcements` | Main channel — all events + general GSA news |
| `gsa-events` | Academic and general events |
| `gsa-food` | Happy hours and food events |
| `gsa-funding` | Grants, awards, and financial aid |
| `gsa-wellness` | Mental health and wellness programs |
| `gsa-research` | Research seminars and workshops |
| `gsa-international` | International student events |

Make sure the bot has **Send Messages** and **Embed Links** permissions in each channel.

If you only want one channel, set all `CHANNEL_*` variables to the same name.

---

## Adding Events

### Preferred: Use /admin_add_event in Discord (recommended)

Run `/admin_add_event` in any Discord channel. A form opens with 5 fields:

| Field | Format | Example |
|---|---|---|
| Event Name | Plain text | `GSA Friday Happy Hour` |
| Date | `YYYY-MM-DD` | `2026-07-04` |
| Time & Location | `time \| location` | `4:00 PM - 7:00 PM \| Highlander Pub` |
| Description | Paragraph | Any text up to 1000 chars |
| Category & RSVP | `category, category \| url` | `food, social \| https://instagram.com/njit.gsa` |

Valid categories: `food`, `social`, `academic`, `funding`, `research`, `international`, `wellness`, `events`, `other`, `general`

**On submit, the bot automatically:**
1. Validates the date format
2. Saves to SQLite and appends to `bot/data/events.yml`
3. Reloads the knowledge base (so `/ask` and `/events` see it immediately)
4. Posts a green "NEW EVENT" embed to the matching category channel(s)
5. Also posts to `#gsa-announcements`
6. Updates `website/data/events.json`
7. Schedules 7-day, 1-day, and 1-hour reminders (automatic — no action needed)

### Alternative: Edit events.yml manually

Use this for bulk edits or when the bot is offline.

1. Edit `bot/data/events.yml` — copy an existing block:
```yaml
- name: "Event Name"
  date: "2026-09-01"
  time: "6:00 PM – 8:00 PM"
  location: "Building, Room, NJIT"
  description: "Description here."
  organizer: "Your name or committee"
  rsvp_link: "https://njit.campuslabs.com/engage/organization/gsa"
  category: "social"
```

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

> Note: Events added via YAML will appear in `/events` and on the website but will **not** get automatic reminders. Reminders only fire for events added via `/admin_add_event`.

---

## Automatic Reminders

The bot checks for upcoming events every **30 minutes** (configurable via `REMINDER_CHECK_INTERVAL`) and sends:

| Reminder | When | Embed colour |
|---|---|---|
| 7-day reminder | 7 days before the event date | Blue |
| 1-day reminder | Day before the event | Orange |
| 1-hour reminder | Within 1 hour of start time | Red |

Each reminder is sent **only once** per event (tracked in SQLite — once sent, the flag is set and it won't fire again).

The bot also posts a **daily digest** to `#gsa-announcements` at 9 AM UTC listing all events in the next 7 days. The digest is skipped if no events are coming up that week.

### Disable reminders for a specific event

```bash
source .venv/bin/activate
python3 -c "
import sqlite3
conn = sqlite3.connect('gsa_gateway.db')
conn.execute(\"UPDATE events SET reminder_sent_7d=1, reminder_sent_1d=1, reminder_sent_1h=1 WHERE name='Event Name Here'\")
conn.commit()
conn.close()
print('Reminders disabled.')
"
```

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

The events page (`website/events.html`) fetches `website/data/events.json` at runtime. That file is auto-generated — never edit it by hand.
- When you use `/admin_add_event` in Discord, `events.json` is updated automatically.
- When you edit `events.yml` manually, regenerate with: `python scripts/export_events_json.py`, then deploy.

---

## Ollama AI

### What it does

When `OLLAMA_ENABLED=true`, the bot:
- Passes the student's question + top 4 KB matches to llama3
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
sudo journalctl -u gsa-gateway --no-pager | grep -i ollama  # shows init messages
```

---

## Discord Admin Commands

Assign the `GSA Officer` role in Discord to any officer. All commands are ephemeral (only the officer who runs them can see the response).

| Command | What it does |
|---|---|
| `/admin_add_event` | Opens a form — adds event, posts announcement, schedules reminders |
| `/admin_announce #channel message` | Post a GSA announcement embed to any channel |
| `/admin_summary` | AI-generated themed summary of last 7 days of initiatives and feedback |
| `/admin_export initiatives` | Download CSV of all initiative submissions |
| `/admin_export feedback` | Download CSV of all feedback messages |
| `/admin_export questions` | Download CSV of all questions asked |
| `/admin_stats` | Total counts and top search topics |

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

Expected: **104 tests pass**. Runtime: ~0.3 seconds.

Run this any time you change bot code to catch regressions before restarting the live service.

---

## Database Maintenance

```bash
# List all events added via /admin_add_event
source .venv/bin/activate
python3 -c "
import sqlite3
conn = sqlite3.connect('gsa_gateway.db')
for r in conn.execute('SELECT id, name, date, announcement_sent, reminder_sent_7d, reminder_sent_1d, reminder_sent_1h FROM events ORDER BY date'):
    print(r)
conn.close()
"

# Delete a test or duplicate event by name
python3 -c "
import sqlite3
conn = sqlite3.connect('gsa_gateway.db')
conn.execute(\"DELETE FROM events WHERE name='Test Event Name'\")
conn.commit(); conn.close(); print('Deleted.')
"
```

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

# 7. Create Discord channels
# Create: gsa-announcements, gsa-events, gsa-food, gsa-funding,
#         gsa-wellness, gsa-research, gsa-international
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
    "bot.commands.mycommand",
]
```

3. Restart the bot. The command syncs to Discord automatically.

Available services inside any cog:
- `self.bot.db` — database (SQLite CRUD)
- `self.bot.kb` — knowledge base (loaded FAQ, events, contacts, resources)
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
| Bot offline, no logs | Run `sudo journalctl -u gsa-gateway -n 50` |
| Ollama not responding | Run `sudo systemctl start ollama`; or set `OLLAMA_ENABLED=false` |
| `/ask` is very slow | Ollama is thinking — normal for llama3 (30–60s). Switch to `llama3.2:1b` for speed |
| Admin commands denied | Role name must match `ADMIN_ROLE_NAME` in `.env` exactly — case-sensitive |
| Website not updating | Run `bash scripts/deploy_website.sh` and wait ~1 min |
| YAML turns red in editor | Use spaces not tabs; 2 spaces before `-`, 4 spaces before field names |
| Database is locked | Another process has `gsa_gateway.db` open — restart the bot |
| `/admin_add_event` — "No announcement channels found" | Create the Discord channels listed in the Announcement Channels section above |
| Reminders not firing | Check `sudo journalctl -u gsa-gateway \| grep scheduler` — the event must have been added via `/admin_add_event`, not just YAML |
| Daily digest not posting | Check that `#gsa-announcements` channel exists and the bot has Send Messages permission |
| Duplicate events on website | Events added via modal AND in YAML both appear. Remove the DB duplicate: see Database Maintenance above, then re-export and deploy. |

---

## Project File Map

```
gsa-gateway/
├── MANUAL.md                 ← You are here
├── README.md                 Project overview (for GitHub)
├── .env                      Your secrets and settings — never commit this
├── .env.example              Template for .env — safe to commit
├── requirements.txt          Python dependencies
├── gsa_gateway.db            SQLite database (auto-created)
│
├── bot/
│   ├── main.py               Entry point — GSABot class, loads all cogs + scheduler
│   ├── config.py             Reads .env into a typed Config object
│   ├── commands/
│   │   ├── ask.py            /ask — fuzzy search + Ollama AI answer
│   │   ├── events.py         /events (list) and /event [name] (detail)
│   │   ├── initiative.py     /initiative — 5-field modal form → SQLite
│   │   ├── feedback.py       /feedback — anonymous message → SQLite
│   │   ├── resources.py      /resources [category]
│   │   ├── contact.py        /contact [role]
│   │   ├── help_cmd.py       /help
│   │   └── admin.py          /admin_add_event (modal form + auto-announce)
│   │                         /admin_summary /admin_export /admin_stats
│   │                         /admin_announce
│   ├── services/
│   │   ├── database.py       All SQLite operations; events table; SHA-256 hashing
│   │   ├── knowledge_base.py Loads gsa_faq.md, events.yml, contacts.yml, resources.yml
│   │   ├── search.py         Fuzzy search via rapidfuzz (60% raw / 45% Ollama threshold)
│   │   ├── moderation.py     Rate limiter, channel allowlist, admin check
│   │   ├── ollama_client.py  Ollama HTTP wrapper — generate_answer(), check_connection()
│   │   ├── summaries.py      weekly_summary() and generate_ai_summary()
│   │   ├── scheduler.py      Background tasks — reminders every 30 min, digest at 9 AM UTC
│   │   ├── channels.py       Channel routing — maps event categories to Discord channels
│   │   └── announcements.py  Announcement embed formatter (new/7d/1d/1h)
│   ├── data/
│   │   ├── gsa_faq.md        Edit to add/update FAQ entries ← edit this
│   │   ├── events.yml        Edit to add/update events ← edit this (or use /admin_add_event)
│   │   ├── contacts.yml      Edit to update officer info ← edit this
│   │   ├── resources.yml     Edit to add/update resource links ← edit this
│   │   └── rules.md          Community guidelines (reference only)
│   └── tests/
│       ├── conftest.py       Shared fixtures (in-memory DB, sample KB)
│       ├── test_search.py    Fuzzy search tests
│       ├── test_database.py  SQLite CRUD and privacy tests
│       ├── test_commands.py  Rate limiter, admin role, channel allowlist
│       ├── test_ollama.py    Ollama client tests (all mocked)
│       └── test_scheduler.py Reminder logic, channel routing, announcement embeds
│
├── website/                  Static site — GitHub Pages
│   ├── index.html            Home page
│   ├── about.html            GSA mission and officers
│   ├── events.html           Loads events.json at runtime via fetch()
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
│   ├── deploy_website.sh         Push website/ to gh-pages branch on GitHub
│   ├── export_events_json.py     Sync events.yml + SQLite → website/data/events.json
│   ├── export_weekly_summary.py  Print or save the 7-day summary
│   ├── init_db.py                Create SQLite tables (safe to re-run)
│   ├── gsa-gateway.service       systemd unit file (copy to /etc/systemd/system/)
│   └── run_bot.sh                Manual start script (alternative to systemd)
│
└── docs/
    ├── architecture.md           How the pieces fit together
    ├── deployment.md             Server migration full guide
    ├── admin_guide.md            Discord admin command reference + channel setup
    ├── privacy_policy.md         Data handling and SHA-256 hashing policy
    └── student_usage_guide.md    Guide for students using the bot
```

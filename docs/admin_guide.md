# Admin Guide — GSA Gateway

All admin commands are ephemeral (only visible to the officer who runs them).
They require the Discord role named in `ADMIN_ROLE_NAME` (default: `GSA Officer`).

## Admin Commands

| Command | What it does |
|---|---|
| `/admin_summary` | 7-day summary: initiatives, feedback, stats |
| `/admin_export [table]` | CSV file attachment (questions / initiatives / feedback) |
| `/admin_stats` | Aggregate counts + top search topics |
| `/admin_announce [channel] [message]` | Post an announcement embed to any channel |
| `/admin_add_event` | Open the event form — posts announcement automatically |

## Assigning the Admin Role
In Discord server settings → Roles → create (or find) the role named exactly as `ADMIN_ROLE_NAME` in `.env`. Assign it to officers.

## Reviewing Initiatives
Run `/admin_summary` or `/admin_export initiatives` to download a CSV. Initiatives are stored with `status = 'pending'` — you can manually update the database if you want to track follow-up.

## Sending Announcements
```
/admin_announce channel:#gsa-announcements message:Reminder — Coffee with GSA is tomorrow at 10 AM!
```
The bot posts a red-bordered embed with the GSA footer. It does not post as plain text.

## Exporting Data
```
/admin_export table:initiatives    # all initiative submissions
/admin_export table:feedback       # all feedback messages
/admin_export table:questions      # all search queries
```
The exported CSV omits raw Discord IDs — all user identifiers are hashed.

## Weekly Summary Script (local)
```bash
python scripts/export_weekly_summary.py            # print to terminal
python scripts/export_weekly_summary.py --save     # save to exports/
python scripts/export_weekly_summary.py --days 14  # 2-week window
```

## Updating Events
Use `/admin_add_event` in Discord for new events — it handles everything automatically.
To bulk-edit or fix events, edit `bot/data/events.yml` and restart the bot.

---

## Setting Up Announcement Channels

### What Discord channels to create

Create these text channels in your server (exact names matter):

| Channel name | Purpose |
|---|---|
| `gsa-announcements` | All events + general announcements (always posted to) |
| `gsa-events` | Academic, social, and general events |
| `gsa-food` | Food events and happy hours |
| `gsa-funding` | Funding, grants, and financial aid events |
| `gsa-wellness` | Mental health, wellness programs |
| `gsa-research` | Research seminars, thesis workshops |
| `gsa-international` | International student events |

### How to configure channel names in .env

If you name your channels differently, update `.env`:
```
CHANNEL_ANNOUNCEMENTS=your-channel-name
CHANNEL_EVENTS=your-events-channel
CHANNEL_FOOD=your-food-channel
...
```
Then restart the bot: `sudo systemctl restart gsa-gateway`

### How /admin_add_event works now

Run `/admin_add_event` in Discord. A form pops up with 5 fields:

| Field | Format | Example |
|---|---|---|
| Event Name | Plain text | `GSA Friday Happy Hour` |
| Date | YYYY-MM-DD | `2026-07-04` |
| Time & Location | `time \| location` | `4:00 PM - 7:00 PM \| Highlander Pub` |
| Description | Paragraph | Any text |
| Category & RSVP | `category, category \| url` | `food, social \| https://...` |

Valid categories: `events`, `food`, `funding`, `wellness`, `research`, `international`, `social`, `academic`, `other`, `general`

On submit the bot:
1. Validates the date format (YYYY-MM-DD)
2. Saves to SQLite and appends to `bot/data/events.yml`
3. Reloads the knowledge base so `/ask` and `/events` see it immediately
4. Posts a green "NEW EVENT" embed to the matching category channel(s)
5. Also posts to `#gsa-announcements`
6. Updates `website/data/events.json`
7. Confirms to you (ephemeral) which channels received the announcement

### How reminders work automatically

The bot checks for upcoming events every **30 minutes** and sends:
- **7-day reminder** — blue embed posted to the event's channel
- **1-day reminder** — orange "Tomorrow:" embed
- **1-hour reminder** — red "Starting Soon:" embed

Each reminder is sent only once (tracked in the database).

The bot also posts a **daily digest** at **9:00 AM UTC** to `#gsa-announcements` listing all events in the next 7 days. The digest is skipped if no events are coming up.

### Configuring the digest schedule

In `.env`:
```
DAILY_DIGEST_HOUR=9     # UTC hour (0-23)
DAILY_DIGEST_MINUTE=0
REMINDER_CHECK_INTERVAL=30   # minutes between reminder checks
```

### How to disable reminders for a specific event

Reminders are only sent for events added via `/admin_add_event` (stored in SQLite).
Events in `events.yml` that were there before this feature was added do NOT get automatic reminders.

To disable reminders for a specific SQLite event, update the database directly:
```bash
sqlite3 gsa_gateway.db \
  "UPDATE events SET reminder_sent_7d=1, reminder_sent_1d=1, reminder_sent_1h=1 WHERE name='Event Name';"
```

## Updating FAQ
1. Edit `bot/data/gsa_faq.md` — follow the `## Q: ... **A:** ...` format strictly
2. Restart the bot

## Updating Contacts / Resources
1. Edit `bot/data/contacts.yml` or `bot/data/resources.yml`
2. Restart the bot

---

## Ollama AI Integration

### What Ollama does

When `OLLAMA_ENABLED=true`, the bot uses a local LLM (llama3) to generate natural-language answers for `/ask` instead of returning raw FAQ text. It also generates a themed, AI-written summary for `/admin_summary` that groups student submissions by topic and suggests action items.

**Key guarantees:**
- Ollama is NEVER called without retrieved FAQ context. It summarises what the knowledge base already says — it cannot invent information outside those boundaries.
- If Ollama is unavailable, the bot falls back silently to the plain KB text. No crash, no error shown to students.

### Check if Ollama is running

```bash
systemctl status ollama        # if installed as a service
ollama list                    # lists downloaded models
curl http://localhost:11434/api/tags   # direct health check
```

If not running, start it:
```bash
ollama serve &
```

### Switch models

1. Pull the new model: `ollama pull llama3.2`
2. Edit `.env`: `OLLAMA_MODEL=llama3.2`
3. Restart the bot: `sudo systemctl restart gsa-gateway`

Available model sizes (bigger = slower but smarter):
- `llama3` — recommended (4.7 GB, good quality)
- `llama3.2:1b` — faster, lighter (1.3 GB, lower quality)
- `mistral` — good alternative (4.1 GB)

### Disable Ollama

Edit `.env`:
```
OLLAMA_ENABLED=false
```
Restart the bot. The bot reverts to returning raw KB text for `/ask` and the plain list format for `/admin_summary`. No other changes needed.

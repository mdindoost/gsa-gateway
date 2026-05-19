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
| `/admin_add_event` | Instructions for editing events.yml |

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
1. Edit `bot/data/events.yml`
2. Run `python scripts/export_events_json.py` to sync the website
3. Restart the bot (or wait for it to pick it up at next startup)

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

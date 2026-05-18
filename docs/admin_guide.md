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

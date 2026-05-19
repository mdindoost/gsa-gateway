# GSA Gateway

**Discord bot + static website for NJIT's Graduate Student Association.**

GSA Gateway is an AI-assisted student communication platform that makes GSA information, events, resources, and student ideas accessible to all NJIT graduate students through Discord slash commands and a public website.

**Live website:** https://mdindoost.github.io/gsa-gateway/
**GitHub repo:** https://github.com/mdindoost/gsa-gateway
**Discord:** https://discord.gg/Ya4XvTE6A

> For how to run, maintain, and extend this project — see **[MANUAL.md](MANUAL.md)**.

---

## Features

| Command | What it does |
|---|---|
| `/ask` | AI-powered Q&A from the GSA knowledge base (llama3 via Ollama) |
| `/events` | List all upcoming GSA events, sorted by date |
| `/event [name]` | Full details for a specific event |
| `/initiative` | Submit a student initiative or idea (anonymous by default) |
| `/feedback` | Send a private anonymous message to GSA officers |
| `/resources [category]` | Browse 8 categories of curated student resources |
| `/contact [role]` | Look up GSA officers and key NJIT campus offices |
| `/help` | Full command reference |
| `/admin_summary` | AI-generated weekly summary of student submissions (officers only) |
| `/admin_export` | Download CSV of initiatives, feedback, or questions (officers only) |
| `/admin_stats` | Engagement stats and top search topics (officers only) |
| `/admin_announce` | Post an announcement embed to any channel (officers only) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot | Python 3.11+, discord.py 2.x, SQLite, rapidfuzz |
| AI | Ollama (llama3, local — no API costs, no data leaves the machine) |
| Website | Pure HTML/CSS/JS, GitHub Pages |
| Tests | pytest, 65 tests, ~0.2s runtime |
| Process management | systemd |

---

## Project Structure

```
gsa-gateway/
├── bot/
│   ├── commands/        One file per slash command
│   ├── services/        Database, search, knowledge base, Ollama, rate limiter
│   └── data/            Edit these YAML/Markdown files to update content
├── website/             Static site — deploy with one command
├── scripts/             Maintenance scripts and systemd service file
└── docs/                Architecture, deployment, admin guide, privacy policy
```

---

## Privacy

User IDs are SHA-256 hashed before any database write. Raw Discord IDs are never stored. See [docs/privacy_policy.md](docs/privacy_policy.md).

---

## License

MIT © 2026 NJIT Graduate Student Association.
Built by Mohammad Dindoost, VP Academic Affairs.

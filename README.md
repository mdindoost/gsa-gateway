<div align="center">

<!-- Drop a logo or hero banner here when ready: <img src="docs/assets/banner.png" width="640"/> -->

# GSA Gateway

![Version](https://img.shields.io/badge/version-2.0-blueviolet?style=for-the-badge)

### The AI assistant for graduate life at NJIT — on every app you already use.

*Ask anything about your Graduate Student Association — officers, funding, events, deadlines, faculty, research — and get an instant, trustworthy answer on Discord, Telegram, or GroupMe. It never makes things up, because every answer is grounded in verified GSA and NJIT information.*

<br/>

![Discord](https://img.shields.io/badge/Discord-live-5865F2?logo=discord&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-live-26A5E4?logo=telegram&logoColor=white)
![GroupMe](https://img.shields.io/badge/GroupMe-live-00AFF0?logoColor=white)
![Self-hosted](https://img.shields.io/badge/100%25-self--hosted-success)
![No cloud bill](https://img.shields.io/badge/cloud%20cost-%240-22c55e)
![License](https://img.shields.io/badge/license-MIT-green)

<br/>

[**🌐 Website**](https://mdindoost.github.io/gsa-gateway/) ·
[**💬 Discord**](https://discord.gg/a4mvbEmSAq) ·
[**✈️ Telegram Bot**](https://t.me/njit_gsa_bot) ·
[**👥 Telegram Group**](https://t.me/GSAGateWayNJIT) ·
[**💬 GroupMe**](https://groupme.com/join_group/115501633/qd1TpFHa)

</div>

---

## Why GSA Gateway

Grad students have questions all the time — *When's the next travel award deadline? Who do I email about my stipend? Which professor works on machine learning? Is there free food this week?* The answers exist, but they're scattered across websites, PDFs, group chats, and people's memory.

GSA Gateway puts all of it one message away. Students ask in plain language on whatever app they already have open, and get a clear, accurate answer in seconds — no menus, no commands, no digging.

It's built on three principles:

1. **Grounded — never invented.** Every answer is drawn from verified GSA documents and official NJIT pages. If the assistant doesn't know, it says so and points to the right office. It will not guess.

2. **Meet students where they are.** One assistant, one brain, three platforms — Discord, Telegram, and GroupMe — all answering identically. No new app to install.

3. **Self-hosted and private.** Runs entirely on one machine with a local AI model. No cloud subscription, no per-question bill, and no student's identity ever stored in the clear.

---

## What it does

✅ &nbsp;**Answers grad-life questions** in plain language — officers, funding, events, policies, deadlines, campus resources, plus YWCC faculty and their research.

✅ &nbsp;**Works across Discord, Telegram, and GroupMe** from a single source of truth, so every student gets the same answer everywhere.

✅ &nbsp;**Runs the 3-Minute Research Pitch competition end to end** — judges score from their phones, the audience votes, and the leaderboard is live and exportable.

✅ &nbsp;**Gives officers a no-code dashboard** to update knowledge, manage clubs and people, post announcements, and watch what students are asking.

✅ &nbsp;**Handy extras** — generates branded GSA QR codes for flyers, broadcasts announcements to every platform at once, and posts live World Cup scores.

---

## Ask it anything

> *"Who are the GSA officers?"*
> *"When is the next travel award deadline?"*
> *"Which CS professors work on machine learning?"*
> *"How do I start a new graduate club?"*
> *"Is there any free food on campus this week?"*
> *"Who do I contact about my stipend?"*

Just type it like you'd text a friend. No commands to memorize.

---

## What it knows

| Area | Coverage |
|---|---|
| **GSA** | Officers, executive board, registered clubs & RGOs, funding, events, and policies |
| **YWCC** | Ying Wu College of Computing faculty, staff, and research areas — kept current automatically |
| **The wider NJIT web** | A live fallback can pull verified answers straight from njit.edu *(optional — off by default)* |

When something falls outside what it knows for certain, it doesn't bluff — it tells the student and routes them to the office that owns the answer. Sensitive topics like immigration, billing, and funding always come with a "confirm with the official office" note.

---

## For GSA officers

Everything is managed from a private, local **admin dashboard** — no code, no spreadsheets, no SQL:

- ✏️ **Knowledge & people** — add or update clubs, officers, faculty, policies, and FAQs
- 📣 **Announcements** — write once, publish to Discord, Telegram, and GroupMe together
- 🏆 **Judging** — create an event, load presenters, hand out judge PINs, open voting, watch results live
- 📊 **Insights** — see what students are asking and where the knowledge has gaps

---

## Quick start

```bash
git clone https://github.com/mdindoost/gsa-gateway.git
cd gsa-gateway

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # add your Discord / Telegram tokens
bash scripts/run_bot.sh     # start the assistant on every platform
```

The admin dashboard comes up alongside the bot at `http://localhost:5555`.
Full setup and operations guide: [**docs/PROJECT_STATUS.md**](docs/PROJECT_STATUS.md).

---

## Under the hood

GSA Gateway is a v2, database-first rewrite: a knowledge graph plus a retrieval pipeline that combines structured lookups with grounded AI generation, served to three chat platforms from one shared brain — all running locally on open-source models.

*A full technical breakdown (architecture, retrieval pipeline, data model) is coming in a dedicated technical README. For now, see [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) and [`CLAUDE.md`](CLAUDE.md).*

---

## Maintainer

Built and maintained by **Mohammad Dindoost** — VP Academic Affairs, NJIT Graduate Student Association.

## License

[MIT](LICENSE) © 2026 NJIT Graduate Student Association

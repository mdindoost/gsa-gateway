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

[![Website](https://img.shields.io/badge/Website-1a73e8?style=for-the-badge&logo=googlechrome&logoColor=white)](https://mdindoost.github.io/gsa-gateway/)
[![Discord](https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/a4mvbEmSAq)
[![Telegram Bot](https://img.shields.io/badge/Telegram_Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/njit_gsa_bot)
[![Telegram Group](https://img.shields.io/badge/Telegram_Group-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/GSAGateWayNJIT)
[![GroupMe](https://img.shields.io/badge/GroupMe-00AFF0?style=for-the-badge&logo=groupme&logoColor=white)](https://groupme.com/join_group/115501633/qd1TpFHa)

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
bash scripts/restart.sh     # start the assistant on every platform
```

The admin dashboard comes up alongside the bot at `http://localhost:5555`.
Full setup and operations guide: [**docs/PROJECT_STATUS.md**](docs/PROJECT_STATUS.md).

---

## Architecture — how it actually works

GSA Gateway is **not** "another RAG chatbot." Its core idea is **gated structured retrieval**: a question is answered by a *deterministic, complete* path whenever possible, and only falls through to probabilistic semantic search for the open-ended long tail. That gate is what makes the answers trustworthy enough to put in front of students.

### Two knowledge layers, one database

Everything lives in a single self-hosted SQLite file (`gsa_gateway.db`) with two complementary representations of the same knowledge:

| Layer | Tables | Powers |
|---|---|---|
| **Text / semantic** | `knowledge_items` (decomposed prose chunks, **FTS5** full-text) + `knowledge_vectors` (**sqlite-vec** `vec0`, `nomic-embed-text` 768-d, L2-normalized) | semantic RAG over unstructured prose |
| **Graph / structured** | `nodes` (Person · Org · ResearchArea) + `edges` (`has_role` w/ titles+category · `researches` · `part_of`) | precise, complete relational answers |

A person isn't one blob — they're decomposed into discrete items (profile, research areas, education, roles, …) and graph edges, so the system can answer *facets* precisely.

### The retrieval pipeline (the gate)

```
                       ┌─────────────────────────────────────────────┐
   user question  ───▶ │  1. STRUCTURED ROUTER  (rule-based, no LLM) │
                       │     enumerate? filter? count? role? entity? │
                       └───────────────┬──────────────┬──────────────┘
                              matches  │              │ no clear match
                       ┌───────────────▼───┐          │
                       │ 2. SQL SKILLS     │          │
                       │ + ENTITY LAYER    │          │
                       │ complete &        │          │
                       │ deterministic:    │          │
                       │ • faculty in X    │          │
                       │ • dean of Y       │          │
                       │ • all "Michaels"  │          │
                       │ • X's research    │          │
                       │ • entity card     │          │
                       │ • disambiguate    │          │
                       └─────┬─────────────┘          │
                  empty/none │  answer                │
                             ▼                         ▼
                       ┌──────────────────────────────────────────────┐
                       │ 3. HYBRID SEMANTIC RAG (fallback, long tail)  │
                       │   sqlite-vec KNN  +  FTS5 BM25                 │
                       │        └──── Reciprocal Rank Fusion ────┐      │
                       │                                cross-encoder   │
                       │                                  rerank        │
                       │                                    │           │
                       │            Ollama llama3.1:8b  ◀────┘           │
                       │        grounded ONLY in retrieved context      │
                       └───────────────┬───────────────────────────────┘
                                       │ KB miss
                       ┌───────────────▼───────────────────────────────┐
                       │ 4. LIVE njit.edu FALLBACK (optional, dormant)  │
                       │    fetch page → answer from VERBATIM spans     │
                       └────────────────────────────────────────────────┘
```

**1 — Structured router** (`v2/core/retrieval/router.py`): pure rule-based slot extraction (no LLM — a small local model is unreliable at orchestration). It maps a question to a parameterized skill *only* when the shape is unambiguous; otherwise it returns `None` and stays out of the way. Conservative by design — a descriptive question wrongly forced into a skill is the dangerous failure.

**2 — SQL skills + entity layer** (`skills.py`, `entity.py`): parameterized SQL over the graph/relational data returning **complete sets**, not a top-K sample — "who is the dean of YWCC", "list *all* the Michaels", "what does Guiling Wang research", "who works on graph neural networks". Named people resolve to a full **entity card** (roles, research, education, contact); ambiguous names (5 "Wang"s) **disambiguate instead of guessing**. Empty results fall through to RAG rather than dead-ending — so a missing structured fact never blocks the prose path.

**3 — Hybrid semantic RAG** (`retriever.py`): the fallback for open prose. The query is embedded and run through **sqlite-vec KNN** (semantic) *and* **FTS5 bm25** (keyword); the two rankings are fused with **Reciprocal Rank Fusion**, reranked by a **cross-encoder**, and the top context is handed to **Ollama `llama3.1:8b`**, which generates an answer **grounded strictly in the retrieved rows** — with an *entity-grounding* rule that forbids attributing one person's facts to another and requires honest "I couldn't find that" over invention.

**4 — Live fallback** (extractive, optional): on a KB miss the bot can search njit.edu, fetch the top page, and answer from **verbatim page-grounded spans + a source link** — never paraphrased into a hallucination. Currently dormant (no search key); degrades silently to a "contact the office" deflection.

**Safety rails throughout:** high-stakes topics (immigration, billing, funding) append a "confirm with the official office" heads-up; user IDs are hashed before any write; answers cite their source document.

### Knowledge ingestion

- **Crawler** (`v2/core/ingestion/explore.py`) — a bounded BFS over server-rendered NJIT pages builds the people/roles/orgs/research-areas graph + KB. **Multi-college** (YWCC + Martin Tuchman, same NJIT profile template, one parser) and **re-runnable**: a re-crawl reconciles departures/moves/new hires automatically (the "M3" reconcile). Adding a college = one entry point.
- **Manual** — GSA officers, clubs/RGOs, and policy prose are authored through the dashboard (gsanjit.com is Wix and not crawlable). Every row is tagged by `source` (`crawler` vs `dashboard`) so an automated re-crawl never clobbers hand-curated data.

### Stack

| Concern | Technology |
|---|---|
| Language / runtime | **Python 3.11+**, asyncio |
| Datastore | **SQLite** (single file, STRICT tables, WAL) |
| Vector search | **sqlite-vec** (`vec0`), 768-d, L2-normalized |
| Keyword search | **SQLite FTS5** + bm25 |
| Fusion / rerank | **Reciprocal Rank Fusion** + **cross-encoder** reranker |
| Generation | **Ollama** · `llama3.1:8b` (local) |
| Embeddings | **`nomic-embed-text`** (local) |
| Chat platforms | **discord.py** · **python-telegram-bot** · **GroupMe** (one shared brain, connector pattern) |
| Dashboard | dependency-free HTML/JS + **sql.js**, served by a stdlib HTTP backend |
| Hosting | **100% self-hosted**, local models — no cloud inference, $0 per-query |

> Deeper design docs live in [`docs/superpowers/specs/`](docs/superpowers/); current state + handover in [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md); conventions in [`CLAUDE.md`](CLAUDE.md).

---

## Maintainer

Built and maintained by **Mohammad Dindoost** — VP Academic Affairs, NJIT Graduate Student Association.

## License

[MIT](LICENSE) © 2026 NJIT Graduate Student Association

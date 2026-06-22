<div align="center">

<!-- Drop a logo or hero banner here when ready: <img src="docs/assets/banner.png" width="640"/> -->

# GSA Gateway

![Version](https://img.shields.io/badge/version-2.1-blueviolet?style=for-the-badge)

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

> *"Which CS professors work on machine learning?"*
> *"Who is the dean of YWCC?"*
> *"What is the Business Data Science PhD application deadline?"*
> *"Who do I contact about my stipend?"*
> *"Who are the GSA officers?"*
> *"When is the next travel award deadline?"*
> *"How do I start a new graduate club?"*
> *"Is there any free food on campus this week?"*

Just type it like you'd text a friend. No commands to memorize.

---

## What it knows

| Area | Coverage |
|---|---|
| **GSA** | Officers, executive board, registered clubs & RGOs, funding, events, and policies |
| **YWCC** | Ying Wu College of Computing — faculty, staff, and research areas, kept current automatically |
| **Martin Tuchman School of Management** | Faculty, administration, and graduate programs (MSM, TECH MBA, Ph.D. in Business Data Science) |
| **The wider NJIT web** | A live fallback (via the **Brave Search API**) pulls a verified answer straight from njit.edu **with a clickable source link** *(active in the live deployment; optional/off for a fresh clone)* |

When something falls outside what it knows for certain, it doesn't bluff — it tells the student and routes them to the office that owns the answer. Sensitive topics like immigration, billing, and funding always come with a "confirm with the official office" note.

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

### Two knowledge stores: a Knowledge Base + a Knowledge Graph

The same knowledge is stored two complementary ways, in one self-hosted SQLite file (`gsa_gateway.db`). This dual representation is what lets the bot be both *fluent* (prose) and *exact* (facts).

**📚 Knowledge Base (KB) — the text/semantic store.**
Unstructured prose, **decomposed** into small focused items — each person becomes separate `profile`, `research_areas`, `education`, `teaching`, `about` chunks rather than one blob; policies/FAQs/event info are chunked too. Every chunk is indexed two ways: **FTS5** (keyword/bm25) and a **sqlite-vec** `vec0` embedding (`nomic-embed-text`, 768-d, L2-normalized). The KB answers *"what is written about X"* — fuzzy, semantic, open-ended questions.
> Tables: `knowledge_items` (chunks + FTS5) · `knowledge_vectors` (vectors).

**🕸️ Knowledge Graph (KG) — the structured/relational store.**
The same people and orgs as **typed entities and relationships**: `Person`, `Org`, `ResearchArea` nodes joined by `has_role` (with title + category), `researches`, and `part_of` edges. The KG answers *"what are the exact facts and relationships of X"* — who is the dean, every faculty member in a department, what someone researches, the org hierarchy — **completely and deterministically**, things a text search structurally can't (enumerate all, traverse, see a role that lives only as an edge).
> Tables: `nodes` (entities) · `edges` (typed relationships, with `category`/`attrs.titles`).

The graph is small and explicit — three node types and four edge types:

| Edge (relation) | From → To | Captures | Powers queries like |
|---|---|---|---|
| **`has_role`** | Person → Org | a role: `category` (officer · faculty · admin · staff · advisor · emeritus) + free-text `attrs.titles` | "who is the dean of YWCC", "GSA officers", "everyone in Informatics" |
| **`researches`** | Person → ResearchArea | a research interest | "who works on graph neural networks", "what does X research" |
| **`part_of`** | Org → Org | the org hierarchy | "departments in YWCC", subtree scoping ("…in the whole college") |
| **`has_source`** | node → doc | provenance — which crawled page/doc a fact came from | re-crawl reconciliation, traceability |

Roles being *edges* (not text) is what lets the bot answer "who is the dean" exactly — and distinguish "Dean" from "Associate Dean" — which a text search can't.

**Why both:** decomposing a person across KB chunks + KG edges means the system can answer a precise *facet* ("X's research areas") from the graph, fall back to prose ("X's bio") from the KB, and never has to cram an entire person into one retrieved blob. The retrieval gate below uses the **KG for relational/precise** asks and the **KB for semantic** ones.

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
                       │ 4. LIVE njit.edu FALLBACK (optional, live)     │
                       │    fetch page → answer from VERBATIM spans     │
                       └────────────────────────────────────────────────┘
```

**0 — Intent gate** (`bot/services/intent_detector.py`): before any retrieval, each message is classified — `question` · `statement` · `greeting` · `farewell` · `thanks` · `help` · `identity` · `clear_history` · `free_mode`/`gsa_mode` (mode switch) · `food` · `social`. Conversational and command-like messages get the right handling instead of being forced through retrieval (a "hi" gets the welcome, "clear" wipes the session, a food question routes to the food-events lookup). Only genuine questions flow into the pipeline below. *(Structured retrieval is actually tried just before this, so "list all CS faculty" is never mis-classified as a statement.)*

**1 — Router** (`v2/core/retrieval/`): as of **Kavosh v2.1**, one **Unified Router** decides the path — a masked coarse-family classifier (a single embedding pass) sorts each question into KG · RAG · LIVE · command · clarify, then hands KG questions to the deterministic resolver below. This replaced four separately-maintained routing mechanisms with one classify-then-resolve path, and its anti-fabrication guarantee is *structural* — the classifier only picks the family; the SQL skill stays deterministic, so it can't invent. The resolver itself is **pure rule-based** (no LLM orchestration — a small local model is unreliable at it) and maps a question to a parameterized skill *only* when the shape is unambiguous; otherwise it falls through to RAG. Conservative by design — a descriptive question wrongly forced into a skill is the dangerous failure.

**2 — SQL skills + entity layer** (`skills.py`, `entity.py`): parameterized SQL over the graph/relational data returning **complete sets**, not a top-K sample — "who is the dean of YWCC", "list *all* the Michaels", "what does Guiling Wang research", "who works on graph neural networks". Named people resolve to a full **entity card** (roles, research, education, contact); ambiguous names (5 "Wang"s) **disambiguate instead of guessing**. Empty results fall through to RAG rather than dead-ending — so a missing structured fact never blocks the prose path.

**3 — Hybrid semantic RAG** (`retriever.py`): the fallback for open prose. The query is embedded and run through **sqlite-vec KNN** (semantic) *and* **FTS5 bm25** (keyword); the two rankings are fused with **Reciprocal Rank Fusion**, reranked by a **cross-encoder**, and the top context is handed to **Ollama `llama3.1:8b`**, which generates an answer **grounded strictly in the retrieved rows** — with an *entity-grounding* rule that forbids attributing one person's facts to another and requires honest "I couldn't find that" over invention.

**4 — Live njit.edu fallback** (extractive, optional): on a KB miss the bot searches njit.edu via the **Brave Search API**, fetches the top page, and answers from **verbatim, page-grounded spans plus the real source link** — so the student gets a clickable njit.edu URL to verify, and the bot never paraphrases into a hallucination (a span that isn't literally on the page is dropped). The search provider is isolated/swappable (Brave today, any provider tomorrow). **Live in the deployment** (`LIVE_ENABLED=1` with a Brave key, free spend-cap); a fresh clone runs with it off, degrading silently to a "contact the office" deflection.

**Safety rails throughout:** high-stakes topics (immigration, billing, funding) append a "confirm with the official office" heads-up; user IDs are hashed before any write; answers cite their source document.

### Knowledge ingestion

- **Crawler** (`v2/core/ingestion/explore.py`) — a bounded BFS over server-rendered NJIT pages builds the people/roles/orgs/research-areas graph + KB. **Multi-college** (YWCC + Martin Tuchman, same NJIT profile template, one parser) and **re-runnable**: a re-crawl reconciles departures/moves/new hires automatically (the "M3" reconcile). Adding a college = one entry point.
- **Manual** — GSA officers, clubs/RGOs, and policy prose are authored through the dashboard (gsanjit.com is Wix and not crawlable). Every row is tagged by `source` (`crawler` vs `dashboard`) so an automated re-crawl never clobbers hand-curated data.

### Beyond retrieval

**Conversation memory + follow-up resolution.** Each user has an in-memory session (last 5 turns, 60-minute TTL). As of v2.1, a follow-up like *"what is his position"* or *"what about for BME?"* is **rewritten into a standalone query using that history *before* routing and retrieval** — so the context reaches the *search*, not just the wording. A deterministic guard (any entity the rewrite adds must appear literally in the history, else it passes the original through) keeps it from resolving to the wrong person.

**Multi-platform publishing.** A *content-generator → scheduler → connector-registry* pattern: any source enqueues a post and the scheduler fans it out to every enabled platform (Discord · Telegram · GroupMe) **in parallel**. The live **World Cup tracker** (a burst-and-rest poller with a fully-tested state machine) and a daily fixtures digest are the reference generators.

**Feedback, analytics & a self-measuring accuracy loop.** Answers carry 👍 / 👎 / 🔄 ("try again") controls; ratings and questions are logged and surfaced in the dashboard. On top of that, the system **watches its own quality**: a daily **failure digest** pushes the recent 👎 (with reason tags) and low-confidence questions to a private admin channel, and a reusable **eval harness** (`scripts/eval.sh`) runs a curated golden set through the *real* pipeline, auto-judges accuracy with the local model, and can run as a **pass/fail gate** (`--min-correct`) to catch regressions before they ship.

The platform also includes the **AVA Judging system**.

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
| Live web fallback | **Brave Search API** → njit.edu, extractive (verbatim spans + source link); provider-isolated/swappable |
| Chat platforms | **discord.py** · **python-telegram-bot** · **GroupMe** (one shared brain, connector pattern) |
| Dashboard | dependency-free HTML/JS + **sql.js**, served by a stdlib HTTP backend |
| Hosting | **100% self-hosted**, local models — no cloud inference, $0 per-query |

> Deeper design docs live in [`docs/superpowers/specs/`](docs/superpowers/); current state + handover in [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md); conventions in [`CLAUDE.md`](CLAUDE.md).

---

## The name

*GSA Gateway* is the product; each generation of the assistant carries a Persian codename.

| Generation | Codename | Meaning | Status |
|---|---|---|---|
| v1 | **Binesh** (بینش) | "insight" | retired June 2026 |
| v2 | **Kavosh** (کاوش) | "exploration / discovery" | **current — v2.1** |

Brand first, codename second — students just say "GSA Gateway"; "Kavosh v2.x" in the technical sections refers to the current generation of the retrieval brain.

---

## Maintainer

Built and maintained by **Mohammad Dindoost** — VP Academic Affairs, NJIT Graduate Student Association.

## License

[MIT](LICENSE) © 2026 NJIT Graduate Student Association

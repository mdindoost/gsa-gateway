# GSA Gateway — Claude Code Session Guide

> **New here / handover?** Read `docs/PROJECT_STATUS.md` for current state (what's shipped,
> deferred, abandoned) and an index of the design docs. This file is the architecture +
> conventions reference.

## Project Summary
Discord + Telegram assistant + dashboard + static website for NJIT's Graduate Student
Association (GSA), plus an NJIT/YWCC knowledge-graph gathering pipeline.
- **Bots**: Python 3.11+, discord.py 2.x + python-telegram-bot, SQLite (+ sqlite-vec), Ollama.
- **Maintainer**: Mohammad Dindoost (VP Academic Affairs). Always-on local machine; the
  dashboard is reached over an SSH tunnel to `localhost:5555`.
- **Single source of truth**: `gsa_gateway.db` (SQLite). The repo is `v2` — the older v1
  (ChromaDB + rapidfuzz + the `/ask /events /initiative /resources` command surface) was
  **cut** (see `docs/superpowers/plans/2026-06-10-phase0-v1-v2-cut.md`). Ignore any
  lingering v1 references.

## Architecture (v2)

**Two knowledge layers, one DB:**
- **Text layer** — `knowledge_items` (plain-text chunks; generated `search_text`; FTS5) +
  `knowledge_vectors` (sqlite-vec `vec0`, `nomic-embed-text`, 768-d, L2-normalized).
  Powers semantic RAG.
- **Graph layer** — `nodes` (Person / Org / ResearchArea) + `edges` (`has_role` w/ a
  `category` and `attrs.titles`, `part_of`, `researches`, `has_source`). `Org` nodes bridge
  the `organizations` tree via `attrs.org_id`. Powers precise structured queries.

**Retrieval** (`v2/core/retrieval/`):
1. `router.py` — deterministic, rule-based. Maps a question to a structured skill ONLY when
   it's clearly enumerate/filter/traverse/count; else returns None → semantic RAG. Resolves
   the org by name / slug / parenthetical-acronym / `metadata.aliases`.
2. `skills.py` — parameterized SQL skills: `faculty_in_department`, `people_by_research_area`,
   `count_people_by_research_area`, `areas_in_org`, `area_counts`, `people_by_area_tag`,
   `officers_in_org` (officer/deprep roles), `people_in_org` (all roles).
3. `structured_answer.py` — runs the routed skill → complete deterministic answer.
4. `retriever.py` (`V2Retriever`) — hybrid semantic (sqlite-vec KNN) + keyword (FTS bm25),
   fused with RRF. **Default answer corpus excludes `publication` + `webpage`** types
   (admin-tunable via `retriever.exclude_types`). `event_info` gets a small boost. (The old
   `contact` boost was removed.)
5. Generation: Ollama `llama3.1:8b`, grounded in the retrieved rows/chunks.
6. **High-stakes heads-up** (`bot/core/headsup.py`): immigration/billing/funding answers get a
   "confirm with <office>" line appended.
7. **Live njit.edu fallback** (`bot/core/live_fallback.py` + `v2/integration/njit_search.py` +
   `v2/core/ingestion/grounded_extract.py`): on a KB miss (top rerank relevance < `LIVE_THRESHOLD`
   or no chunk), search njit.edu (Brave Search API), fetch the top page, and answer from **verbatim,
   page-grounded spans + source link** (extractive — no hallucination; spans must appear literally
   on the page or are dropped). Gated by `LIVE_ENABLED`. **Currently OFF (`LIVE_ENABLED=0`, Brave
   subscription cancelled) → dormant, degrades silently to the "contact the office" deflection.**
   Re-enable: add a search key (Brave or swap provider — `njit_search` is provider-isolated) +
   `LIVE_ENABLED=1`. Spec: `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md`.

**Knowledge sources:**
- **YWCC / NJIT** — gathered by the `explore()` crawler (`v2/core/ingestion/explore.py`)
  over server-rendered NJIT pages (`computing.njit.edu/people`, etc.). Builds people/roles/
  orgs/research-areas. `source='crawler'`. Re-crawl + M3 reconcile handles turnover.
- **GSA + RGOs/clubs** — **manual**, `source='dashboard'`. gsanjit.com is Wix and not
  crawlable (see `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md`). Officers/
  clubs are authored via the dashboard People editor (or a small backend create); prose
  comes from primary docs as `knowledge_items`.

**Bots** (`bot/main.py` loads cogs): **all-conversational** — the only slash command is
`/qrcode` (Discord) / `/qrcode` + `/start` (Telegram). Everything else is answered by chat
(`bot/commands/chat.py` `on_message` → `bot/core/message_handler.py`, which calls the
structured router then the v2 retriever via `v2/integration/retriever_shim.py`). WorldCup is
a separate live-scores integration.

**Dashboard** — `dashboard/` (vanilla JS + sql.js, loads the whole DB via `/db`) served by
`v2/local_server.py` (HTTP on `127.0.0.1:5555`). Tabs: Overview, Posts, KB, **People (KG)**
(add/edit/remove people + roles + clubs), Analytics, Settings, **Jobs** (control plane: run
the crawler / refresh / embed as subprocess jobs), **Judging** (create events, load presenter
CSV, manage judges + PINs, open/close, live progress, leaderboard, score drill-down, export).
Writes go through `POST` endpoints (`/orgs /knowledge /people /people/remove /settings /posts`)
or an offline `changes.sql`. Judging writes go through `/judging/events/…` (live API, not sql.js).

## File Map (v2)

```
gsa-gateway/
├── bot/
│   ├── main.py                 Entry point; loads cogs (EXTENSIONS=[qrcode_cmd]) + worldcup + chat
│   ├── commands/
│   │   ├── qrcode_cmd.py        /qrcode (Discord) — branded QR; uses bot/services/qr.py
│   │   ├── chat.py              on_message — free-form Q&A in #ask-gsa + DMs (the main UX)
│   │   └── worldcup.py          /worldcup — live scores (separate feature)
│   ├── connectors/telegram_connector.py   Telegram bot (only /start + /qrcode; rest conversational)
│   ├── core/
│   │   ├── assistant.py         Wires the V2RetrieverShim + Ollama
│   │   └── message_handler.py   Routes a message: structured router -> else RAG pipeline
│   ├── services/
│   │   ├── qr.py                Shared branded-QR generation (Discord + Telegram)
│   │   ├── database.py          SQLite CRUD + hash_user_id()
│   │   └── chunker.py           tiktoken chunker (<=350-token chunks; reused by v2 doc ingest)
│   └── data/                    GSA-owned content (faq retired; contacts.yml seed; sources/gsa/*.md)
├── v2/
│   ├── core/
│   │   ├── database/schema.py   Single source of truth for v2 tables (STRICT; create_all/get_connection)
│   │   ├── graph/               store.py (node/edge upsert), orgs.py (ensure_org/sync_org_nodes/
│   │   │                        org_node_id), project.py (project_appointment/project_entity)
│   │   ├── ingestion/           explore.py (crawler), reconcile.py, discovery.py, entry_points.py,
│   │   │                        roster.py (roster->KG), gsa_docs.py (doc->KB),
│   │   │                        people_editor.py (dashboard add/edit/remove person+role+bio)
│   │   └── retrieval/           router.py, skills.py, structured_answer.py, retriever.py, embedder.py
│   ├── core/judging/           db.py (CRUD), session.py (state machine), calculator.py (leaderboard/export)
│   ├── integration/            retriever_shim.py, scheduler_runner.py, match_watcher.py, telegram_client.py
│   ├── publishing/             publisher.py, connectors/ (registry + discord/telegram/stub)
│   ├── scripts/                embed_all.py (resumable embed), rebuild_index.py
│   └── local_server.py         Dashboard HTTP backend (GatewayHandler) on :5555
├── dashboard/                  app.js + index.html + style.css (sql.js in-browser)
├── scripts/
│   ├── run_bot.sh              Starts Telegram + Discord bots
│   ├── run_explore.py          Run the YWCC crawler (explore()) — --depth/--reset/--frontier
│   ├── verify_kg.py            Alignment checks: verify_kg() + verify_gsa()
│   ├── _area_tag_migrate.py    hardened_backup() lives here (used by every gated write)
│   ├── gsa_ingest_people.py    Roster YAML -> KG (gated)
│   ├── gsa_ingest_docs.py      bot/data/sources/gsa/*.md -> KB (gated)
│   └── _*_migrate.py           One-off gated migrations (dry-run + hardened backup)
└── docs/
    ├── PROJECT_STATUS.md        ← current state + design-doc index (read first)
    └── superpowers/{specs,plans,findings}/   point-in-time design docs
```

## Key Invariants

- **`source` tags everything.** `'crawler'` (YWCC/NJIT, auto) vs `'dashboard'` (manual GSA/
  clubs). The crawler reconcile and `run_explore.py --reset` only touch `source='crawler'`,
  so manual data is never clobbered.
- **All-conversational.** Only `/qrcode` is a slash command. Don't reintroduce lookup
  commands; route questions through the chat/RAG path.
- **Never insert `search_text`** — it's a generated column (`title || ' ' || content`).
- **Embeddings**: documents use the `search_document: ` prefix, queries `search_query: `;
  vectors are L2-normalized. After adding `knowledge_items`, run `python v2/scripts/embed_all.py`.
- **Graph-write transactions**: the core helpers (`project_appointment`, `people_editor`,
  `roster`) do NOT commit — the caller (CLI / `local_server` handler) owns the transaction.
- **Gated live writes**: any script that writes the live DB takes a `hardened_backup(...)`
  (online-backup API + integrity check), defaults to dry-run, requires `--commit`.
- **User IDs are hashed** (`hash_user_id`) before any DB write.
- **Org resolution**: orgs resolve by name / slug / parenthetical acronym / `metadata.aliases`.
  Give new clubs a clean short slug (the acronym), like GSA's slug is `gsa`.

## Common Tasks

### Add an RGO / club + officers (manual, `source='dashboard'`)
Dashboard: People tab → **+ New club/org** *(or KB tab → Add Organization for a parent +
clean slug)* → add officers via the Add form → if you added an About/bio, run the embed.
Backend equivalent (gated): `ensure_org(slug=<acronym>, name, parent_slug='gsa', type='club')`
+ `people_editor.add_or_edit_person(...)` per officer + an "About <club>" `knowledge_item`,
then `python v2/scripts/embed_all.py`. Verify: `who are the <X> officers` / `what is <X>`.

### Add / edit KB prose
Dashboard KB tab (or drop a `.md` in `bot/data/sources/gsa/` → `scripts/gsa_ingest_docs.py
--commit`), then `python v2/scripts/embed_all.py`.

### (Re)gather YWCC people
`python scripts/run_explore.py --commit` (or the dashboard Jobs tab). Then `embed_all.py`.
Always followed by `scripts/verify_kg.py`.

### Embed new/changed knowledge
`python v2/scripts/embed_all.py` (resumable — only embeds items missing a vector).

### Restart the bots
`bash scripts/run_bot.sh` (Telegram + Discord). DB-only changes need no restart (bots read
live); code changes do. Discord re-syncs slash commands on startup.

### Add a structured-retrieval skill
Add to `v2/core/retrieval/skills.py`, wire it into `structured_answer.run/format_answer`,
and add a route in `router.py`. (See `officers_in_org` / `people_in_org` as the pattern.)

### Evaluate the bot (coverage + accuracy)
`bash scripts/eval.sh` — runs `eval/questions.txt` through the REAL pipeline (KB + live), classifies
each kb/live/deflect, auto-judges accuracy (local model), removes its own analytics rows, and prints
coverage + accuracy + the gap list. Edit `eval/questions.txt` (one Q/line, `# category` headers) to
add questions. `--limit N` for a quick subset.

### Debug a single query (pipeline X-ray)
`bash scripts/ask.sh "<question>" [--verbose] [--answer]` — shows router decision, fused pool,
reranker CE scores, final top-5, heads-up, and (verbose) the exact LLM prompt / (answer) the real answer.

### Run / test the judging system
Dashboard → Judging tab (requires server mode). Create event → load CSV → add judges → Open.
Telegram: `judge mode` → PIN → participant number → score each criterion → `yes`.
Presenter: `presenter mode` → participant number → confirms name (marks present).
Tests: `python3 -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py v2/tests/test_judging_session.py -q` (69 tests).
Judging tables: `judging_events`, `judging_judges`, `judging_presenters`, `judging_scores`.
Schema migrations are idempotent — `create_all()` on startup applies new columns safely.

### Crawl NJIT pages → KB (grounded, pipeline built; mass-crawl deferred)
`scripts/_crawl_stage.py --bucket <url-substr> --prefix <p>` (sitemap discovery + fetch + clean to
`/tmp/njit_crawl/`) → a Haiku subagent extracts VERBATIM facts to `bot/data/sources/njit-web/*.md`
→ `scripts/_crawl_ground_filter.py --apply` (keeps only lines literally on the page) →
`scripts/_crawl_ingest.py --commit` (gated, `source='crawler'`) → `embed_all.py`. Use selectively;
the live fallback covers the long tail on demand.

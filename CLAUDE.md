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
   `officers_in_org` (officer/deprep roles), `people_in_org` (all roles), `faculty_areas_in_department`
   (per-person research areas for a dept's faculty — the ANTI-FABRICATION skill, see below),
   `top_people_by_metric` (rank an org SUBTREE by a Scholar metric; GROUP BY p.id so multi-role people
   count once; returns the full ranked list + `with_metric`/`total_in_org` for honest-partial wording).
   Entity layer (`entity.py`): `people_by_role`/`role_in_org` (find a person BY their role title),
   `research_of_person`, `entity_card`, `persons_by_lastname` (unambiguous-surname resolution),
   `metric_of_person` (one person's Scholar numbers; `person_attrs` is the single per-person attrs reader).
   **Metrics are a first-class, registry-driven queryable facet:** the router matches metric words via
   `profile_fields.match_metric` (aliases live ON the `Metric` — bare `i10`/`cite` deliberately NOT
   aliased), routes "X citations / X's h-index" → `metric_of_person` and "most cited in <org> / top N by
   h-index in <org>" (org + `_RANK_CUE`) → `top_people_by_metric` (root org = university-wide). Numbers
   are rendered DETERMINISTICALLY and `is_deterministic(result)` makes the caller skip LLM compose so a
   metric is never reworded. Surname/full-name resolution is the shared `_resolve_person`/`_resolve_surname`.
   Spec: `docs/superpowers/specs/2026-06-19-metric-queries-design.md`.
3. `structured_answer.py` — runs the routed skill → complete deterministic answer. `deterministic_suffix(result)`
   appends external-profile **links** (on `entity_card`) / Scholar **metrics** (on `research_of_person`)
   to the FINAL answer VERBATIM, AFTER LLM compose — never handed to the LLM (no hallucinated URLs/numbers).
4. `retriever.py` (`V2Retriever`) — hybrid semantic (sqlite-vec KNN) + keyword (FTS bm25),
   fused with RRF. **Default answer corpus excludes ONLY `publication`** (volume: ~77% of rows,
   one-paper-title chunks from profile decompose — admin-tunable via `retriever.exclude_types`).
   **Recency slice** (`decay_for(row, now, event_boost)` applied at BOTH boost sites — rerank ~:200
   and fusion ~:345, sharing one `now`): `type='news'` decays by age, `max(0.5, 0.85·0.5^(age/180))`
   with a HARD 0.5 floor (demoted, never erased — undated news doesn't decay); crawled `type='event'`
   boosts UPCOMING only (`event_end>=now`), past events decay unboosted; `type='webpage'` served at a
   0.8 prior (downweighted, not excluded); GSA-curated `type='event_info'` keeps its unconditional 1.2×
   boost. (The old `contact` boost was removed; the high-stakes heads-up — see #6 — was removed 2026-06-25.)
5. Generation: Ollama `llama3.1:8b`, grounded in the retrieved rows/chunks. `compose_from_rows`
   (`bot/services/ollama_client.py`) rephrases the structured Facts at temp 0.0 — it MUST NOT add,
   drop, invent, attach an unlisted attribute to a name, or elaborate a listed one (anti-fabrication
   clauses live here). NOTE: a friendly "Hi there!" opener on "tell me about X" answers is INTENTIONAL
   (Mohammad likes it) — phrasing-driven, facts always correct; do NOT strip it.
   **Anti-fabrication rule (honest-partial):** if the user asks for an attribute the retrieval doesn't
   have for those entities, NEVER let the LLM fill the gap. Route to the data we DO have, state what's
   missing. e.g. "research areas of the professors in X" → `faculty_areas_in_department` (lists ONLY
   people who list areas, "N of the {org} faculty list research areas: …"; degrades to a names roster +
   "I don't have research areas listed" line when nobody does) — was fabricating a topic per name.
6. **High-stakes heads-up — REMOVED (Mohammad, 2026-06-25).** Formerly immigration/billing/funding
   answers got a "⚠️ confirm with <office>" line appended (`bot/core/headsup.py`). **Barrier removed:**
   NJIT web content is public + authoritative and users understand the bot can err — the caution
   conflicted with the verbatim / never-withheld hard lines. No money / I-20 / student-info gate or
   heads-up is appended; answers stand on the source link. (Removal = drop the two `apply_headsup`
   call sites in `message_handler.py` + retire `headsup.py` and its tests.) See [[feedback_remove_headsup_barrier]].
7. **Live njit.edu fallback** (`bot/core/live_fallback.py` + `v2/integration/njit_search.py` +
   `v2/core/ingestion/grounded_extract.py`): on a KB miss (top rerank relevance < `LIVE_THRESHOLD`
   or no chunk), search njit.edu (Brave Search API), fetch the top page, and answer from **verbatim,
   page-grounded spans + source link** (extractive — no hallucination; spans must appear literally
   on the page or are dropped). Gated by `LIVE_ENABLED`. **Currently ON (2026-06-20) — `LIVE_ENABLED=1`
   + a new `BRAVE_API_KEY` on Brave's "Search" tier (NOT "Answers" — we do our own extraction), Free
   spend-cap (~1,000 req/mo free credits; pauses → degrades to the deflection if exhausted, never a
   bill).** Provider-isolated (`njit_search` — swap the provider in one module). Off-switch: set
   `LIVE_ENABLED=0`. Spec: `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md`.

**Knowledge sources:**
- **YWCC + MTSM / NJIT** — gathered by the `explore()` crawler (`v2/core/ingestion/explore.py`)
  over server-rendered NJIT pages. Builds people/roles/orgs/research-areas. `source='crawler'`.
  **Multi-college:** the crawler walks every anchored root in
  `entry_points.ALL_ENTRY_POINTS` — YWCC (`computing.njit.edu/people` hub) **and** MTSM
  (`management.njit.edu/faculty` → `mtsm`, `/administration` → `mtsm-administration`). All NJIT
  people use the same `people.njit.edu/profile/<slug>` template, so one parser serves all
  colleges; **adding a college = add its `EntryPoint`(s)** (see the MTSM design doc). Order:
  a sub-unit listing must follow the listing that creates its parent org (ensure_org resolves
  parent by slug; MTSM_FACULTY creates `mtsm` before MTSM_ADMIN creates `mtsm-administration`).
  **Re-crawl is a first-class, repeatable op:** `python scripts/run_explore.py --commit` re-walks
  all roots; M3 reconcile (once, after the whole loop) retires departures and re-files moves;
  `--reset` re-derives from scratch (crawler rows only). Invariant: **MTSM has no
  `type='department'` children** (its faculty file under the `mtsm` college itself) — `verify_kg`
  enforces it. MTSM "Leadership" people are NOT reappointed to the college (would collide with
  their faculty@mtsm edge); they stay `admin@mtsm-administration` + `faculty@mtsm`.
- **GSA + RGOs/clubs** — **manual**, `source='dashboard'`. gsanjit.com is Wix and not
  crawlable (see `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md`). Officers/
  clubs are authored via the dashboard People editor (or a small backend create); prose
  comes from primary docs as `knowledge_items`.
- **College/department PROSE (Crawling 2.1)** — the `college_crawl` engine
  (`v2/core/ingestion/college_crawl.py`) DFS-walks a college/dept subdomain and brings
  ALL prose (program/student/advising/news/event pages) → `knowledge_items`. **Second engine,
  separate from `explore.py`:** explore.py owns PEOPLE (`source='crawler'`); college_crawl owns
  PROSE (`created_by='college_crawl'`, distinct so reconcile — which is created_by-scoped — never
  cross-wipes). It crawls bare-host subdomain seeds, **skips people-listing paths** (segment-match
  on `entry_points.SUPPLEMENTARY_PATHS`, so `/faculty` is skipped but `/faculty-handbook` kept) so a
  name-dump never competes with structured KG answers, **mechanically types** each page by URL
  segment (`type='news'`/`newsroll` for /news,/newsroll,/announcement(s); `type='event'` for /event(s);
  else `policy`), and captures dates from STRUCTURED markup only (`<meta article:published_time>` /
  JSON-LD / `<time>`). People-skip is segment-match AND strips a Drupal pager suffix `-<digits>` so a
  paged roster alias (`/administration-0`) is skipped too (a `/faculty-handbook` is still kept).
  **Add a college/dept = append a `ProseEntry(seed, org_slug, org_name, parent_slug, org_type)` to
  `PROSE_ENTRY_POINTS`** (college root `org_type='college'`, depts `'department'`) — no new code.
  Runner: `scripts/crawl_college.py [--entry <slug>] [--commit] [--embed]` (gated; dev-copy + hardened_backup;
  each entry independently recrawlable). **ALL NJIT colleges LIVE (2026-06-25): YWCC (pilot) + MTSM +
  NCE + 6 eng depts + CSLA + 6 sci/liberal-arts depts + theater + HCAD = 21 entries, ~1,274 prose rows
  embedded.** Schools without their own subdomain (HCAD's NJSOA/Art+Design, MTSM administration) carry
  their prose on the parent college org (prose is host-scoped; the PEOPLE layer still splits them).
  Serving recency (news decay w/ floor, upcoming-event boost, webpage downweight) lives in the retriever (see #4 below).
  Spec: `docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md`.
- **External profiles (links + Scholar metrics)** — per-person `attrs.profiles` bag on the Person
  node: `{scholar/linkedin/orcid/github/website: {url, …}}` + Scholar metrics `scholar.{citations,
  h_index, i10_index, updated_at}`. Registry `v2/core/people/profile_fields.py` is the SINGLE source
  of truth for which fields exist + how they render (one row = one field). Crawler AUTO-captures any
  scholar/linkedin/orcid/github/website link present on the NJIT profile page into `attrs.profiles`
  (`project.py project_entity` MERGES, never clobbers manual metrics). Scholar METRICS are MANUAL /
  on-demand (NJIT pages don't list Scholar): give me a name + Scholar URL → I WebFetch the public
  page → `people_editor.set_person_profiles`. `v2/core/ingestion/scholar.py` + `scripts/refresh_scholar.py`
  (gated) are the refresh mechanism (provider-isolated `default_fetch`; Scholar blocks bots, so a
  scheduled run needs a sanctioned provider — owner chose manual WebFetch for now, NOT SerpAPI).
  Surfacing: links on identity ("who is X"), metrics on research ("X research") — NOT on lists.
  Spec: `docs/superpowers/specs/2026-06-19-person-external-profiles-design.md`.

**Bots** (`bot/main.py` loads cogs): **all-conversational** — the only slash command is
`/qrcode` (Discord) / `/qrcode` + `/start` (Telegram). Everything else is answered by chat
(`bot/commands/chat.py` `on_message` → `bot/core/message_handler.py`, which calls the
structured router then the v2 retriever via `v2/integration/retriever_shim.py`).

**WorldCup** is a separate live-scores integration. **The live engine is provider-selected via
`WC_PROVIDER` (default `espn`).** `bot/main.py` calls `wc_providers.watcher.make_watcher` under
`V2_WORLDCUP_ENABLED`: `espn` → **`EspnMatchWatcher`** (ESPN `site.api.espn.com` scoreboard — scorer+minute+
OG/pen posts, no API key, scoreboard-primary, goal-identity dedup); `football_data` → the legacy
**`MatchWatcher`** (the one-flag KILL-SWITCH: `WC_PROVIDER=football_data` + restart). `EspnMatchWatcher`
SUBCLASSES `MatchWatcher`, reusing its proven schedule/active-set/tick/posting and overriding only the data
source (`v2/integration/wc_providers/`: `normalize.py`→NormMatch, `espn_process.py`→event-driven state machine,
`espn.py`→fetch+circuit-breaker, `watcher.py`→subclass+factory, `shadow.py`→read-only A/B). Separate state
files per provider. ESPN scorer/half-label come from the scoreboard `details[]` + minute (no `period` in feed);
preview group-table deferred (G7); goal-identity-includes-minute is a monitor-first-matchday known-risk.
Shipped+live+merged 2026-06-24 (`scripts/wc_shadow_compare.py` = the latency A/B). It posts kick-off / goal /
full-time (gated by `FOOTBALL_*` env). It enqueues posts the v2 scheduler delivers. Supporting (live): `match_preview.py` (T-5 preview), `wc_schedule.py`,
`daily_fixtures.py` (9am fixtures digest, `WC_FIXTURES_ENABLED`), and `worldcup_tracker.py` **for its helper
functions only** (`BASE_URL`, `DEBUG_FILE`, `format_event`, `team_label`, `format_standings`). Match status
note: football-data reports in-play as **either `IN_PLAY` or `LIVE`** (varies per match) — `match_watcher.LIVE`
holds both; treat them identically. **DEAD / do-not-use (v1-era, replaced by MatchWatcher — 0 live importers):**
`v2/integration/worldcup_runner.py` (`WorldCupRunner`), `bot/services/worldcup_tracker.py`,
`bot/services/football_client.py`, and the inert `bot/commands/worldcup.py` `/worldcup` cog +
`bot/services/worldcup_embeds.py` (deps never wired). Touch `match_watcher.py` for live-score behavior, NOT
the `bot/services/*` files.

**Modes (unified, `bot/core/modes/`):** 5 user modes through ONE registry — **gsa** (default) +
**free** (general chat, skips GSA knowledge) + **judge / presenter / audience** (judging). `Mode`
enum + `ConversationModeStore` (owns the gsa/free bit) + `ModeRegistry` (the ONE place to ask
"what mode") + `ModeDispatcher` (Telegram's single entry point: judging owns a msg iff already in a
judging mode OR a judging trigger). **Derive-don't-mirror:** judging modes are PROJECTED read-only
via `JudgingSessionManager.mode_of()` — one writer per fact, can't drift. Free mode skips structured
routing (so it isn't identical to gsa). Spec: `docs/superpowers/specs/2026-06-19-unify-modes-design.md`.

**Dashboard** — `dashboard/` (vanilla JS + sql.js, loads the whole DB via `/db`) served by
`v2/local_server.py` (HTTP on `127.0.0.1:5555`). Tabs: Overview, Posts, KB, **People (KG)**
(add/edit/remove people + roles + clubs), Analytics, Settings, **Data Sources** (the control-plane
tab — UI label; code key still `data-tab="jobs"`/`renderJobs()`: run the crawler / faculty refresh /
Scholar refresh / Scholar discovery / embed as subprocess jobs), **Judging** (create events, load presenter
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
│   │   └── worldcup.py          /worldcup cog — INERT/dead (deps unwired); live engine is v2/…/match_watcher.py
│   ├── connectors/telegram_connector.py   Telegram bot (only /start + /qrcode; rest conversational)
│   ├── core/
│   │   ├── assistant.py         Wires the V2RetrieverShim + Ollama; builds the shared ConversationModeStore
│   │   ├── modes/              Unified mode mgmt: registry.py (Mode/Store/Registry), dispatcher.py
│   │   └── message_handler.py   Routes a message: structured router -> else RAG pipeline (mode-gated)
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
│   │   ├── ingestion/           explore.py (crawler+adaptive discovery), reconcile.py, discovery.py,
│   │   │                        entry_points.py, roster.py, gsa_docs.py, njit_adapter.py (profile
│   │   │                        parser; captures scholar/linkedin/orcid/github/website links),
│   │   │                        people_editor.py (add/edit person + set_person_profiles), scholar.py
│   │   │                        (Scholar metrics parse + refresh)
│   │   ├── people/              profile_fields.py (external-profile registry: render_links/render_metrics)
│   │   └── retrieval/           router.py, skills.py, structured_answer.py, entity.py, retriever.py, embedder.py
│   ├── core/judging/           db.py (CRUD), session.py (state machine), calculator.py (leaderboard/export)
│   ├── integration/            retriever_shim.py, scheduler_runner.py, match_watcher.py, telegram_client.py
│   ├── publishing/             publisher.py, connectors/ (registry + discord/telegram/stub)
│   ├── scripts/                embed_all.py (resumable embed), rebuild_index.py
│   └── local_server.py         Dashboard HTTP backend (GatewayHandler) on :5555
├── dashboard/                  app.js + index.html + style.css (sql.js in-browser)
├── scripts/
│   ├── restart.sh              Stop+start ALL services (Discord+Telegram+GroupMe, Ollama, dashboard)
│   ├── run_explore.py          Run the NJIT crawler (explore(), YWCC + MTSM) — --depth/--reset/--frontier
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
- **HARD LINE — post records are immortal** (Mohammad, 2026-06-23): every `posts` / `post_deliveries`
  row is kept **forever** — the permanent audit of who sent what, how, where, and when. Auto-deletion
  (the scheduled-deletion feature) removes ONLY the delivered message FROM the platform (Telegram/
  Discord/GroupMe) and **marks** the DB record (e.g. `deleted_at` / a `deleted` status) — it NEVER
  deletes, anonymizes, or hard-removes the post/delivery rows. "Delete" = unsend on the platform, not
  forget in our DB. Applies even to privacy-sensitive posts (qual-exam notices): the message leaves the
  channel, the record stays.
- **All-conversational.** Only `/qrcode` is a slash command. Don't reintroduce lookup
  commands; route questions through the chat/RAG path.
- **Never insert `search_text`** — it's a generated column (`title || ' ' || content`).
- **Embeddings**: documents use the `search_document: ` prefix, queries `search_query: `;
  vectors are L2-normalized. After adding `knowledge_items`, run `python v2/scripts/embed_all.py`.
- **Graph-write transactions**: the core helpers (`project_appointment`, `people_editor`,
  `roster`) do NOT commit — the caller (CLI / `local_server` handler) owns the transaction.
- **Gated live writes**: any script that writes the live DB takes a `hardened_backup(...)`
  (online-backup API + integrity check), defaults to dry-run, requires `--commit`. Backups rotate to
  the **newest 10 overall** (`.backups/`), never pruning the just-written one or anything < 6h old.
- **User IDs are hashed** (`hash_user_id`) before any DB write.
- **Org resolution**: orgs resolve by name / slug / parenthetical acronym / `metadata.aliases`.
  Give new clubs a clean short slug (the acronym), like GSA's slug is `gsa`.
- **Reconcile is SOURCE-SCOPED.** `reconcile_entity` only diffs/deactivates `knowledge_items` with
  the SAME `created_by`, so a crawler re-run never wipes manual/scholar enrichment sharing a person's
  entity_id (and an EMPTY decomposition never retires a present person's KB — transient-fetch guard).
  Departures drop a fully-departed person's KB across ALL sources.
- **Never fabricate an unheld attribute** (honest-partial) — see the anti-fabrication rule in Retrieval.
- **HARD LINE — evidence before any state claim** (Mohammad, 2026-06-23): NEVER assert the live DB or code is
  untouched / rolled back / in any given state without PROVING it first — checksum/diff/query against a known
  reference (e.g. a `.backups/` snapshot, `git log`, a row count). (Caught: I twice claimed the live DB was
  untouched during the office-tier rollback; a checksum vs the pre-office backup proved I HAD written it.)
- **HARD LINE — NJIT web content is served VERBATIM, never withheld** (Mohammad, 2026-06-23): NJIT-sourced
  content is authoritative — serve the real text VERBATIM. NEVER stage/withhold/decline/redact, and NEVER drop
  or alter a figure that literally appears on the page. The source link covers staleness. (Strengthens
  [[feedback_njit_source_keep_live]] into a hard line; the decline-for-volatile branch that violated this is why
  the whole office-shadowing build was rolled back 2026-06-23.)
- **HARD LINE — crawl/recrawl is data-bringing ONLY, separated from usage** (Mohammad, 2026-06-23): the crawl/
  recrawl layer's SOLE job is to bring data from the web, clean it, and put it into KB or KG.
  - **Cleaning = MECHANICAL only** (allowed): strip HTML/markup, nav/boilerplate/scripts/styles, control &
    garbage characters, fix encoding/whitespace. The actual human-readable text — sentences, wording, numbers,
    figures, order — passes through UNCHANGED.
  - **FORBIDDEN: any human-style rewriting** — no summarizing, paraphrasing, rewording, condensing, truncating,
    "improving", or selecting/dropping content for meaning. If a human would call it editing, the crawler must
    not do it.
  - **No usage decisions in the crawler** — no serving/gating/staging/decline/`is_active` logic. HOW data is
    used belongs to the retrieval/serving layer, strictly separate. One direction: clean → store.
- **EXPERT-REVIEW HARD GATE** (Mohammad, 2026-06-19): build/ship NOTHING non-trivial — including bug
  FIXES — without (a) a senior-engineer review AND, for retrieval/answer changes, a RAG/LLM-researcher
  review, AND (b) Mohammad's approval. Flow: design → expert review(s) → he approves → build TDD →
  show the diff → he signs off → commit + restart. Even small/surgical fixes. Dispatch reviewers as
  background general-purpose agents with the concrete artifact + file paths; relay findings, don't
  rubber-stamp. See memory `feedback-senior-eng-review`.
  - **CHECK DESIGN/BUILD AGAINST THE PLAN (Mohammad, 2026-06-20):** the review is not just diff-level
    correctness — the reviewer (and I, before claiming done) MUST verify the work against the spec's
    STATED GOALS: every goal/bullet the plan listed is either shipped or **explicitly, loudly flagged
    as deferred** — never silently dropped. Every reviewer prompt includes "check the implementation
    against the design's goals; list which shipped vs deferred." Every spec/PR ends with a goals
    checklist (shipped/deferred). This closes the gap that let the external-profiles spec's bullet 3
    (Scholar interests → ResearchArea/`researches` edges) ship unbuilt and unflagged. See
    `feedback_review_against_plan`.

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

### (Re)gather crawler people (YWCC + MTSM) — repeatable refresh
`python scripts/run_explore.py` walks ALL colleges in `ALL_ENTRY_POINTS` and refreshes the KG
(people/roles/research-areas) + crawler KB. **This is the recurring update path** — re-run it
whenever NJIT pages change (new hires, departures, role/research changes); M3 reconciles
turnover automatically. Gated workflow: dev copy first (`cp gsa_gateway.db /tmp/dev.db;
run_explore.py --db /tmp/dev.db`), inspect + `scripts/verify_kg.py`, then live, then
`v2/scripts/embed_all.py`. `--reset` re-derives crawler data from scratch (manual/dashboard
content untouched). MTSM program/PhD/FAQ **prose** is separate + manual: `scripts/mtsm_ingest.py
--commit` (`source='dashboard'`, idempotent on `natural_key`) — re-run when those pages change.

### Add a person's external profile (Scholar / LinkedIn / ORCID / GitHub / website + Scholar metrics)
Manual / on-demand (NJIT pages don't list Scholar). Given a name + URLs: find the person key
(`SELECT key FROM nodes WHERE type='Person' AND name LIKE …`), for a Scholar URL **WebFetch the public
page** for citations/h-index/i10, then `people_editor.set_person_profiles(conn, person_key=…,
profiles={"scholar":{"url","citations","h_index","i10_index","updated_at"}, "linkedin":{"url"}, …})`
(deep-merges; metric strings coerced to numbers) — gated `hardened_backup` first. DB-only → no restart.
Verify: `who is <name>` (links) / `<name> research` (metrics). Crawler auto-captures any of these links
already on the NJIT page on the next crawl. Refresh job: `scripts/refresh_scholar.py` (dry-run; `--commit`).
Current data: ~57 people with links, ~49 with Scholar metrics (all manual). 27 have metrics but no
research areas → citations stay dormant (only surface on "X research" when areas exist) — accepted.

### Refresh / discover Scholar (dashboard "Data Sources" tab + CLIs) — SHIPPED 2026-06-20
Three jobs around `attrs.profiles.scholar`, all gated (`hardened_backup` + `--commit`, dry-run default).
Dashboard **Data Sources** tab → "Refresh:" dropdown:
- **"Google Scholar (metrics & research areas)"** = the RECURRING refresh: re-pull citations/h-index/i10 +
  interests→research-areas for people who ALREADY have a Scholar URL, scoped by college/dept, with an
  "older than N days" staleness filter (default 30). CLI: `scripts/refresh_scholar.py --org X --older-than N
  --commit --embed`. `select_scholar_targets` / `scholar_scope_list(mode="have")`. updated_at is full YYYY-MM-DD.
- **"Discover Scholar URLs (search + add)"** = the ONE-TIME find: for faculty WITHOUT a URL, Brave-search
  the profile, verify, and AUTO-WRITE only a **verified-`njit.edu`-email + name-match + (unique-surname OR
  dept/interest corroboration)** match (the anti-fab gate, `v2/core/ingestion/scholar_discovery.py`
  `classify_candidate` — the SOLE boundary; homonyms→review CSV, never guessed; provenance-tagged
  `discovered_by/at/match_basis`). CLI: `scripts/discover_scholar.py`. `scholar_scope_list(mode="discover")`
  counts faculty WITHOUT a URL.
- **Slow-drip sweep** (CLI only, long-running): `scripts/discover_scholar_sweep.py --budget N --commit`
  (nohup/detached) — drips ~50/hr across ALL faculty-without-Scholar, required Brave `--budget` (shared
  ~1,000/mo pool w/ the live fallback), block-aware backoff, SIGTERM-safe, embeds once at end.
**Termination/resume marker:** non-strict outcomes write `scholar.discovery_attempted` and
`select_discovery_targets(skip_attempted=True, retry_after_days=)` excludes them → both discovery jobs never
re-search a dead end; `blocked` (transient throttle) is NOT marked. Scholar blocks bots at volume → discovery
is best-effort; full coverage of the ~580 ultimately needs a sanctioned provider (SerpAPI, owner-deferred).
Spec: `docs/superpowers/specs/2026-06-20-scholar-url-discovery-design.md` + `…-discovery-sweep-design.md`.

### Embed new/changed knowledge
`python v2/scripts/embed_all.py` (resumable — only embeds items missing a vector).

### Restart the bots
`bash scripts/restart.sh` (stops + restarts Discord + Telegram + GroupMe, manages Ollama, and
relaunches/verifies the dashboard; kills duplicates first; `--no-llm` to run without Ollama).
DB-only changes need no restart (bots read live); code changes do. Discord re-syncs slash
commands on startup.

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
Full manual: `docs/judging_system.md` (admin guide, all flows, API reference, run checklist).
Dashboard → Judging tab (requires server mode). Create event → load CSV → add judges → Open.
Telegram flows: `judge mode` (PIN → score), `presenter mode` (register attendance),
`audience mode` (anyone votes once; judges auto-return to judge mode after voting).
Admin controls audience voting independently (Open/Close Audience Voting on dashboard).
Tests: `python3 -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py v2/tests/test_judging_session.py -q` (86 tests).
Tables: `judging_events`, `judging_judges`, `judging_presenters`, `judging_scores`, `judging_audience_votes`.
Schema migrations are idempotent — `create_all()` on startup applies new columns safely.

### Crawl NJIT pages → KB (grounded, pipeline built; mass-crawl deferred)
`scripts/_crawl_stage.py --bucket <url-substr> --prefix <p>` (sitemap discovery + fetch + clean to
`/tmp/njit_crawl/`) → a Haiku subagent extracts VERBATIM facts to `bot/data/sources/njit-web/*.md`
→ `scripts/_crawl_ground_filter.py --apply` (keeps only lines literally on the page) →
`scripts/_crawl_ingest.py --commit` (gated, `source='crawler'`) → `embed_all.py`. Use selectively;
the live fallback covers the long tail on demand.

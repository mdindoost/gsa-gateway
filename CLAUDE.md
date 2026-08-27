# GSA Gateway — Claude Code Session Guide

> **New here / handover?** Read `docs/PROJECT_STATUS.md` for current state (what's shipped,
> deferred, abandoned) and an index of the design docs. This file is the architecture +
> conventions reference. **Last verified against the code + live DB: 2026-08-22.**

## Project Summary
Discord + Telegram + GroupMe assistant + dashboard + static website for NJIT's Graduate Student
Association (GSA), plus an NJIT knowledge-graph + prose gathering pipeline.
- **Stack**: Python (venv is 3.14; README says 3.11+), discord.py 2.x + python-telegram-bot +
  a GroupMe poller, SQLite (+ sqlite-vec), Ollama (local models only).
- **Maintainer**: Mohammad Dindoost (VP Academic Affairs). Always-on local machine; the
  dashboard is reached over an SSH tunnel to `localhost:5555`.
- The repo is `v2` — the older v1 (ChromaDB + rapidfuzz + the `/ask /events /initiative
  /resources` command surface) was **cut**. Ignore any lingering v1 references.

## Architecture (v2)

### TWO live databases (Phase-1 split, 2026-06-28)
- **`gsa_gateway.db` — KNOWLEDGE.** `knowledge_items` / `knowledge_chunks` / both vec0 vector
  tables, `nodes`, `edges`, `organizations`, `settings`, analytics. Built by
  `create_knowledge_schema(DB_PATH)`.
- **`gsa_gateway_ops.db` — OPS.** `posts`, `post_templates`, `post_deliveries`, `events`,
  `event_reminders`, all six `judging_*` tables, `area_expand_cache`, `area_vocab_blob`. Built by
  `create_ops_schema(OPS_DB_PATH)`. Spec: `docs/superpowers/specs/2026-06-28-split-ops-db-design.md`.
- ⚠️ `gsa_gateway.db` still physically contains EMPTY legacy copies of the moved tables. They are
  husks — nothing reads or writes them. **Never call `create_all()` against a live DB**: it runs
  both builders on one path and re-creates the MOVED ops tables inside the knowledge DB. It exists
  only for tests/fixtures that want one combined file.
- A dev copy for crawler work only needs the KNOWLEDGE db; publishing/judging work needs the ops db.

### Knowledge layers
- **Text layer** — `knowledge_items` (prose rows; generated `search_text`; FTS5) +
  `knowledge_vectors` (sqlite-vec `vec0`, **`qwen3-embedding:0.6b`, 1024-d**, L2-normalized).
- **Chunk layer** — `knowledge_chunks` (parent_id → knowledge_items ON DELETE CASCADE, ordinal,
  text, content_hash, model_id) + `knowledge_chunk_vectors`. ~38k rows. Used by the DEEP-fallback
  tier (chunk-level KNN); the primary leg is whole-item KNN.
- **Graph layer** — `nodes` (Person / Org / ResearchArea) + `edges`: **`has_role`** (w/ a
  `category` and `attrs.titles`), **`researches`**, **`part_of`** — those three only. `Org` nodes
  bridge the `organizations` tree via `attrs.org_id`.
- **Vector width is descriptor-driven, not a constant.** `schema.vector_table_ddl(descriptor)`
  formats both vec0 CREATEs from `active_descriptor().dim`. A model swap = `recreate_vector_tables()`
  + a full re-embed. vec0 tables have no FKs — `v2/core/database/vector_gc.py` sweeps orphans.

### Models (all local, via Ollama at `localhost:11434`)
| Role | Model | Notes |
|---|---|---|
| Generation | `granite4:tiny-h` | compose temp 0.0, RAG temp 0.3, num_ctx 16384 |
| Embedding | `qwen3-embedding:0.6b` | 1024-d, L2-normalized, chunk 512/overlap 77 |
| Reranking | `Xenova/ms-marco-MiniLM-L-6-v2` | local ONNX cross-encoder, CPU, cached in `models/reranker/` |

`nomic-embed-text` (768-d) is a **registered legacy descriptor only** — uninstalled 2026-07-07,
kept for content_hash lookups on legacy rows. `v2/core/retrieval/model_descriptor.py` is the single
source of truth for model id / dim / prefixes.

### The answer path (`bot/core/message_handler.py` is the one ordered pipeline)
All three connectors funnel into `MessageHandler.handle()`. Order:
1. Rate limit (5/60s per user) → empty-text guard.
2. **Follow-up resume** (`FOLLOWUP_RESUME_ENABLED`) → **context rewrite** (LLM rewrites a follow-up
   into a standalone query; unresolvable antecedent → CLARIFY, `ANTECEDENT_GUARD_ENABLED`).
3. **Query correction** (`QUERY_CORRECT_ENABLED`) — deterministic and narrow: an org-type LEADER
   rule (`who runs X` → chair/dean/president/officers) + a 6-word generic-vocab dictionary that
   deliberately EXCLUDES org slugs. Not typo tolerance, not spell-correction, not LLM rewriting.
4. **Gate-1** (`answer_gate.gate1_intent`) — a high-precision regex cue matcher that deflects
   pre-retrieval: personal / task / other-institution / live cues. Exempted when the structured
   layer can answer.
5. **`UnifiedRouter.decide()`** (`unified_router.py`, `ROUTER_V21=1`, SHADOW=0 → it ACTS): a
   deterministic COMMAND layer → a zero-encode regex FAST-PATH → a coarse-family embedding
   classifier (one encode) → `resolve_kg()` = the deterministic `router.route()`. A miss falls
   through to a constrained-JSON slot-extraction attempt, then to RAG. The legacy `_try_structured`
   branch is only reached when `decide()` returns None/COMMAND.
6. **Structured answer** (`structured_answer.run/format_answer`) — see below.
7. **RAG pipeline** — hybrid retrieve → primary-miss signal → deep chunk-rescue
   (`RETRIEVAL_DEEP_FALLBACK`, threshold 0.30) → live njit.edu fallback → compose → **Gate-2**.

**Structured retrieval** (`v2/core/retrieval/`): `router.py` (the deterministic resolver — rule-based,
org resolution by name / slug / parenthetical acronym / `metadata.aliases`), `skills.py` +
`entity.py` (the SQL skills), `structured_answer.py` (dispatch + rendering).
- **33 skills implemented, 32 routable** (`role_in_org` is superseded by `people_by_role`).
- **16 are in `_DETERMINISTIC_SKILLS`** → `is_deterministic(result)` makes the caller SKIP LLM
  compose entirely: metrics, papers, links, all `*_disambig` CLARIFY rosters, `title_of_person`
  (the `(joint appointment)`/`(affiliated)` markers are load-bearing), `awards/news/bio/involvement_of_person`.
- The other 16 hand complete SQL Facts to `compose_from_rows` (temp 0.0) for rephrasing, guarded by
  `_compose_preserves_facts` (reverts to verbatim Facts if a counted roster or an appointment
  qualifier would be dropped).
- **A joint/affiliated title never answers a role query for THAT org** (`entity.NON_HOME_CATEGORIES`,
  2026-08-26): NJIT listing cards print a person's own title regardless of section, so a
  cross-listing lends its HOME-org title to the wrong org (this made "chair of informatics" return
  Halper AND Oria, who chairs CS). Org-scoped role lookup HARD-EXCLUDES joint/affiliated; org-agnostic
  lookup drops such a row only when the same person already has a home row for that role, else keeps
  it marked "(joint appointment)". Spec: `docs/superpowers/specs/2026-08-26-non-home-role-attribution-design.md`.
- **Org SUBTREE vs EXACT org matters:** `top_people_by_metric` and the research-area skills walk
  `org_descendants` (so "most cited in NCE" spans its departments). **Roster skills
  (`faculty_in_department` / `officers_in_org` / `people_in_org`) scope to the EXACT org node** —
  by design, not a bug.
- `deterministic_suffix(result)` appends external-profile **links** (on `entity_card`) / Scholar
  **metrics** (on `research_of_person`) to the FINAL answer VERBATIM, AFTER compose — never handed
  to the LLM, so a URL or citation count cannot be hallucinated.

**Hybrid retrieval** (`retriever.py`): sqlite-vec KNN (cosine via `max(0, 1 - d²/2)`, valid because
vectors are L2-normalized) + FTS5 bm25, fused with **RRF k=60**, pool 60/leg. Then a local ONNX
cross-encoder rerank whose ranking is RRF-fused back with an ASYMMETRIC constant — **60 for the
fused leg, 10 for the CE leg** — so CE dominates ordering while an exact keyword match keeps a
floor. Rerank is additive-safe (any failure → pure fusion order). Returns `limit=5` entity-diversified
primaries **plus one injected profile card per distinct person** in them.
- **Default answer corpus excludes `publication` AND `syllabus`** (`DEFAULT_EXCLUDE_TYPES`) —
  15,745 + 880 rows, ~60% of the corpus, leaving ~11,060 passages served by a general question.
  Admin-tunable via the `retriever.exclude_types` setting.
- **Recency/type prior** (`decay_for`, applied at BOTH boost sites sharing one `now`): `news` decays
  `max(0.5, 0.85·0.5^(age/180))` from `metadata.published_at` — undated news does NOT decay (sits at
  0.85); crawled `event` boosts UPCOMING only; `webpage` served at a 0.8 prior; GSA-curated
  `event_info` keeps its unconditional 1.2× boost.
- Dead settings row: `retriever.contact_boost` — the contact boost was removed from the code.

**Generation** (`bot/services/ollama_client.py`): `compose_from_rows` rephrases structured Facts at
temp 0.0 — it MUST NOT add, drop, invent, attach an unlisted attribute to a name, or elaborate a
listed one (the anti-fabrication clauses live here). A friendly "Hi there!" opener on person answers
is INTENTIONAL (Mohammad likes it) — do NOT strip it.

**Anti-fabrication (honest-partial):** if the user asks for an attribute retrieval doesn't have,
NEVER let the LLM fill the gap — state what's missing. Implemented PER-SKILL, not as a blanket
property: `faculty_areas_in_department` (lists only people who list areas; degrades to a roster +
"I don't have research areas listed"), `does_person_research_area`, and the profile-field renderers.

**Gate-2 — WS4 abstention / faithfulness** (`answer_gate.py` + `faithfulness.py`, `ANSWER_GATE_ENABLED=1`):
runs POST-generation, inside the RAG path only (skipped for food/social intents; structured answers
are covered by determinism + `_compose_preserves_facts` instead). Deterministic checks first
(self-decline, subjective-superlative guard, and every count/rate/amount/date in the answer must
appear in the evidence); only the residual costs one temp-0.0 JSON call that judges whether the
CONTEXT answers the QUESTION (`FULLY_SUPPORTED` / `PARTIALLY_SUPPORTED` / `NOT_IN_CONTEXT`) and must
return a quote, which is then grounding-checked (0.7 token-set overlap). **The model call never sees
the generated answer.** On fail → try the live tier if untried → else a useful abstain.
**Fail-open by design:** a gate exception or transport failure KEEPS the composed answer; a non-empty
unparseable response abstains. Measured (frozen 45-Q set, vs the PREVIOUS production gate, not vs
no gate): false-answer 40%→15%, false-abstain 24%→20%; an ungated pipeline scored 65% false-answer.
⚠️ Only `gate1_intent`, `gate2_prompt`, `parse_gate2` are imported by production — the rest of
`answer_gate.py` (`gate_decision`, `verify_support`, `quote_grounded`, `is_fact_shaped`) is used
ONLY by `scripts/eval_gate_shadow.py` / `trace_query.py`.

**Live njit.edu fallback** (`bot/core/live_fallback.py` + `v2/integration/njit_search.py` +
`v2/core/ingestion/grounded_extract.py`, `LIVE_ENABLED=1`): on a KB miss (top CE relevance <
`LIVE_THRESHOLD`, default 0.15) Brave-search `<query> site:njit.edu`, fetch the top page(s) (max 2
with the relevance-gate flag off), and answer from **verbatim page-grounded spans + source link**
(spans must appear literally on the page or are dropped; page truncated to 12k chars, ≤6 spans).
Provider-isolated. **This is the one path that sends question text off-machine** (to a search API —
never to a model provider). Off-switch: `LIVE_ENABLED=0`.
⚠️ `LIVE_RELEVANCE_GATE` / `LIVE_OPTIN` are OFF, so the A1 "answer-quality bundle" and the LiveLinks
degrade are unreachable today. The local `office_page` tier is a structural no-op (0 such rows).

**High-stakes heads-up — DELETED (Mohammad, 2026-06-25).** `bot/core/headsup.py` does not exist and
`bot/tests/test_headsup_removed.py` enforces its absence. Answers stand on the source link; never
reintroduce a "confirm with <office>" line. See [[feedback_remove_headsup_barrier]].

### Knowledge sources (who owns what — no overlap)
| Producer | `created_by` | Owns | Runner |
|---|---|---|---|
| People crawler | `crawler` | People / roles / orgs / research areas + per-person KB | `scripts/run_explore.py` |
| www sitemap crawler | `njit_www_crawl` | **Most prose today** (~7.1k rows) | `scripts/crawl_www.py` |
| College prose crawler | `college_crawl` | Subdomain DFS prose (~143 rows) | `scripts/crawl_college.py` |
| Catalog crawler | `catalog_crawl` | Course/program records | `scripts/crawl_catalog.py` |
| Scholar enrichment | `scholar` | Metrics + interests→areas | `scripts/refresh_scholar.py` |
| Manual | `dashboard` | GSA + RGOs/clubs | Dashboard People/KB tabs |

- **People crawler** (`v2/core/ingestion/explore.py`) walks **all 17 roots** in
  `entry_points.ALL_ENTRY_POINTS` — YWCC, MTSM + mtsm-administration, NCE + 6 engineering depts,
  6 science/liberal-arts depts, HCAD. All NJIT people share the `people.njit.edu/profile/<slug>`
  template, so one parser serves every college; **adding a college = add its `EntryPoint`(s)**
  (a sub-unit listing must follow the listing that creates its parent org). M3 reconcile runs once
  after the whole loop: retires departures, re-files moves. Invariant: **MTSM has no
  `type='department'` children** — `verify_kg` enforces it.
- **`college_crawl`** owns `PROSE_ENTRY_POINTS` + the `ProseEntry` dataclass — **both live in
  `v2/core/ingestion/college_crawl.py`, NOT `entry_points.py`** (22 entries). It skips people-listing
  paths (segment-match on `SUPPLEMENTARY_PATHS`, stripping a Drupal pager suffix) so a name dump never
  competes with structured KG answers, **mechanically types** each page by URL segment
  (`_NEWS_SEGMENTS` = news/newsroll/announcement(s) → `news`; event(s) → `event`; else `policy`), and
  captures dates from STRUCTURED markup only (`article:published_time` / JSON-LD / `<time>`).
  `classify_type` is shared with `www_crawl`.
- **`www_crawl`** is 100% sitemap-driven — a static list of 47 sitemap URLs (main + 12 offices +
  12 services + 22 subdomains), no DFS, no host allowlist. `news.njit.edu` is NOT among them.
- **GSA + RGOs/clubs** are manual (gsanjit.com is Wix and not crawlable).
- **External profiles** — per-person `attrs.profiles` bag: `{scholar/linkedin/orcid/github/website/
  facultyfolio: {url, …}}` + `scholar.{citations,h_index,i10_index,updated_at}`.
  `v2/core/people/profile_fields.py` is the SINGLE source of truth for which fields exist and how
  they render. The crawler auto-captures any such link on the NJIT profile page
  (`project_entity` MERGES, never clobbers manual metrics). Live: **900 active people with a
  profiles bag, 364 with a Scholar URL, 363 with metrics** — and it is NOT all manual any more
  (136 carry `scholar.discovered_by`, 464 carry `scholar.discovery_attempted`).
  Surfacing: links on identity ("who is X"), metrics on research ("X research") — NOT on lists.
  **FacultyFolio** is special-cased in `render_links`: when a person has a published folio page it
  returns ONLY that link (it already aggregates the rest).

### Bots
**All-conversational.** Only slash commands: `/qrcode` (Discord + Telegram) and Telegram's mandatory
`/start`. Three live connectors — Discord (`bot/main.py`, EXTENSIONS = `["bot.commands.qrcode_cmd"]`,
message handling in `bot/commands/chat.py`), Telegram (`run_telegram.py`), and **GroupMe**
(`run_groupme.py`, a poller with no command surface). All three build the same assistant and call the
same `message_handler`. All answers get 👍/👎/🔄 buttons (GroupMe excepted — no button surface).

**Modes** (`bot/core/modes/`): 5 modes through ONE registry — **gsa** (default) + **free** (general
chat, skips structured routing) + **judge / presenter / audience** (judging). `Mode` enum +
`ConversationModeStore` (owns the gsa/free bit) + `ModeRegistry` (the ONE place to ask "what mode")
+ `ModeDispatcher` (Telegram's single entry point). **Derive-don't-mirror:** judging modes are
PROJECTED read-only via `JudgingSessionManager.mode_of()` — one writer per fact, can't drift.

**Dashboard** — `dashboard/` (vanilla JS + sql.js) served by `v2/local_server.py` on
`127.0.0.1:5555`. It loads TWO snapshots: `GET /db` (knowledge) and `GET /db-ops` (posts/events/
judging read layer). Nine nav tabs: Overview, Posts, KB, **Organization** *(dead stub — "coming in
Checkpoint D", no `switchTab` branch; delete or build it)*, People (KG), Analytics, Settings,
**Data Sources** (control plane; UI label — code key is still `data-tab="jobs"`/`renderJobs()`),
**Judging**. Writes go through `POST` endpoints (`/orgs /knowledge /people /people/remove /settings
/posts`) or an offline `changes.sql`; judging writes go through `/judging/events/…` (live API).

**WorldCup — DORMANT, do not invest.** The ESPN watcher still starts (`V2_WORLDCUP_ENABLED=true`,
`WC_PROVIDER=espn`) but the tournament is over: last real post 2026-07-19, and **every poll since
2026-08-04 returns HTTP 403** (1,200+ `EspnProvider blocked` lines in `gsa_gateway.log`).
`FOOTBALL_ENABLED=false`, so the `/worldcup` cog never loads. Engine:
`v2/integration/wc_providers/` (`EspnMatchWatcher` subclasses `MatchWatcher`); helpers live in
`v2/integration/worldcup_tracker.py` (the `bot/services/` file of the same name is DEAD).
**To silence it: `V2_WORLDCUP_ENABLED=false` + restart.** Everything under `bot/services/worldcup_*`
and `bot/services/football_client.py` is v1-era dead code — delete rather than document.

## File Map (v2)

```
gsa-gateway/
├── bot/
│   ├── main.py                Discord entry; EXTENSIONS=["bot.commands.qrcode_cmd"]
│   ├── commands/              chat.py (on_message — the main UX) · qrcode_cmd.py · worldcup.py (INERT)
│   ├── connectors/            base.py · telegram_connector.py · groupme_connector.py
│   │                          (discord_connector.py here is a STUB — Discord lives in commands/chat.py)
│   ├── core/                  message_handler.py ← THE pipeline · assistant.py · modes/
│   │                          live_fallback.py · live_query.py · context_rewrite.py · followup_match.py
│   │                          pending.py · identity.py · deflection.py · answer_render.py · msg_split.py
│   ├── services/              ollama_client.py · database.py (hash_user_id, WAL) · qr.py · chunker.py
│   └── data/                  GSA content: gsa_faq.md (STILL LOADED), contacts.yml, events.yml,
│                              resources.yml, rules.md, sources/gsa/*.md
├── v2/
│   ├── core/
│   │   ├── database/          schema.py (create_knowledge_schema / create_ops_schema /
│   │   │                      vector_table_ddl / recreate_vector_tables) · vector_gc.py
│   │   ├── graph/             store.py · orgs.py (ensure_org/org_node_id) · project.py
│   │   ├── ingestion/         explore.py · www_crawl.py · college_crawl.py (+PROSE_ENTRY_POINTS)
│   │   │                      catalog_crawl.py · prose_store.py · reconcile.py · entry_points.py
│   │   │                      njit_adapter.py · people_editor.py · scholar.py · scholar_discovery.py
│   │   │                      grounded_extract.py · eos_crawl.py (LIBRARY: extract_prose helpers)
│   │   ├── people/            profile_fields.py (profile/metric registry + render_links)
│   │   ├── judging/           db.py · session.py · calculator.py
│   │   ├── publishing/        publisher.py · scheduler.py · deleter.py · event_projection.py
│   │   ├── connectors/        registry.py + discord/telegram/groupme/stub
│   │   └── retrieval/         unified_router.py (LIVE primary) · router.py (deterministic resolver)
│   │                          skills.py · entity.py · structured_answer.py · retriever.py
│   │                          reranker.py · embedder.py · model_descriptor.py
│   │                          answer_gate.py · faithfulness.py · query_correct.py
│   ├── integration/           retriever_shim.py · njit_search.py (Brave) · scheduler_runner.py
│   │                          wc_providers/ · worldcup_tracker.py (helpers) · failure_digest.py
│   │                          telegram_client.py · discord_client.py · groupme_client.py
│   ├── scripts/               embed_all.py (items) · embed_chunks.py (chunks) · rebuild_index.py
│   └── local_server.py        Dashboard HTTP backend on :5555 (serves /db and /db-ops)
├── dashboard/                 app.js · posts_logic.js · index.html · style.css
├── scripts/                   ~120 files. Key families:
│                              crawl_www.py · crawl_college.py · crawl_catalog.py · run_explore.py
│                              rebuild_prose.py · prose_rebuild_gate.py · ingest_college_leadership.py
│                              refresh_scholar.py · discover_scholar{,_sweep}.py · funding_enrich.py
│                              sync_facultyfolio_links.py · verify_kg.py · embed helpers
│                              eval.sh · autoeval.sh · ask.sh → trace_query.py · restart.sh
│                              llm.sh · health_check.sh · stats.sh
│                              _area_tag_migrate.py ← hardened_backup() lives here
│                              _*_migrate.py — one-off gated migrations (dry-run + backup)
├── eval/                      questions.txt (336 Qs / 79 categories) · results_judged.jsonl
└── docs/
    ├── PROJECT_STATUS.md      ← current state + design-doc index (read first)
    ├── GSA_Gateway_Technical_Report.pdf  (+ report/ source, regenerable)
    └── superpowers/{specs,plans,findings}/   point-in-time design docs
```

## Key Invariants

- **`created_by` tags every knowledge row.** `knowledge_items` has NO `source` column — it has
  `created_by` (`crawler` / `njit_www_crawl` / `college_crawl` / `catalog_crawl` / `scholar` /
  `dashboard` / `migration` / `manual`) plus a `metadata.source` key. `nodes`/`edges` DO have a real
  `source` column. Reconcile and `run_explore.py --reset` only touch crawler-owned rows, so manual
  data is never clobbered.
- **Reconcile is PRODUCER-SCOPED.** `reconcile_entity` only diffs/deactivates rows with the SAME
  `created_by`, so a crawler re-run never wipes manual/Scholar enrichment sharing an entity_id (and
  an EMPTY decomposition never retires a present person — transient-fetch guard). **One deliberate
  exception: departures** drop a fully-departed person's KB across ALL producers. Sitemap-driven
  retirement is additionally floored — a frontier below 300 URLs or below 80% of the prior active
  set skips the retirement pass entirely.
- **Never insert `search_text`** — it's `GENERATED ALWAYS AS (COALESCE(title,'') || ' ' || content) STORED`.
- **Embeddings**: passages are embedded **RAW** (`doc_prefix=""`); only the QUERY gets the
  `Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: `
  wrapper. Vectors are L2-normalized. (The old `search_document: `/`search_query: ` pair was the
  nomic scheme and is DEAD.) **Embedding is TWO passes:** `v2/scripts/embed_all.py` (items) and
  `v2/scripts/embed_chunks.py` (chunk layer) — both resumable.
- **Gated live writes**: gated scripts take a `hardened_backup(...)` (online-backup API + integrity
  check), default to dry-run, and require `--commit`. Backups rotate to the **newest 10 overall**
  (`.backups/`), never pruning the just-written one or anything < 6h old.
  ⚠️ **Two exceptions to know:** `scripts/run_explore.py` has NO `--commit` (it writes whatever
  `--db` points at, defaulting to LIVE; backup is the opt-in `--backup`), and `v2/scripts/embed_all.py`
  writes live with no dry-run.
- **WAL is set by the application connections**, not by the schema module:
  `schema.get_connection()` sets only `foreign_keys=ON` + `busy_timeout=5000`; `PRAGMA journal_mode=WAL`
  is issued by `bot/services/database.py` and `v2/local_server.py`. Most tables are STRICT — the OPS
  `events` table is deliberately NOT, to preserve the live v1 shape.
- **Graph-write transactions**: the core helpers (`project_appointment`, `people_editor`, `roster`)
  do NOT commit — the caller (CLI / `local_server` handler) owns the transaction.
- **User IDs are hashed** (`hash_user_id`) before any DB write.
- **Org resolution**: by name / slug / parenthetical acronym / `metadata.aliases`. Give new clubs a
  clean short slug (the acronym), like GSA's slug is `gsa`.
- **HARD LINE — post records are immortal** (Mohammad, 2026-06-23): every `posts` / `post_deliveries`
  row (now in **`gsa_gateway_ops.db`**) is kept **forever** — the permanent audit of who sent what,
  how, where, and when. Auto-deletion removes ONLY the delivered message FROM the platform and
  **marks** the DB record; it NEVER deletes, anonymizes, or hard-removes the rows. "Delete" = unsend
  on the platform, not forget in our DB. Applies even to privacy-sensitive posts.
- **All-conversational.** Don't reintroduce lookup commands; route questions through the chat/RAG path.
- **Never fabricate an unheld attribute** (honest-partial) — see the anti-fabrication rule above.
- **HARD LINE — evidence before any state claim** (Mohammad, 2026-06-23): NEVER assert the live DB or
  code is untouched / rolled back / in any given state without PROVING it first — checksum/diff/query
  against a known reference (a `.backups/` snapshot, `git log`, a row count).
- **HARD LINE — NJIT web content is served VERBATIM, never withheld** (Mohammad, 2026-06-23):
  NJIT-sourced content is authoritative — serve the real text VERBATIM. NEVER stage/withhold/decline/
  redact, and NEVER drop or alter a figure that literally appears on the page. The source link covers
  staleness.
- **HARD LINE — crawl/recrawl is data-bringing ONLY, separated from usage** (Mohammad, 2026-06-23):
  - **Cleaning = MECHANICAL only**: strip HTML/markup, nav/boilerplate/scripts/styles, control &
    garbage characters, fix encoding/whitespace. The human-readable text — sentences, wording,
    numbers, figures, order — passes through UNCHANGED.
  - **FORBIDDEN: any human-style rewriting** — no summarizing, paraphrasing, rewording, condensing,
    truncating, "improving", or selecting/dropping content for meaning.
  - **No usage decisions in the crawler** — no serving/gating/staging/decline/`is_active` logic. HOW
    data is used belongs to the retrieval/serving layer. One direction: clean → store.
  - *(Known deviation: the frontier pass caps faculty personal-site captures at 6,000 chars; 64 rows
    sit at that cap.)*
- **EXPERT-REVIEW HARD GATE** (Mohammad, 2026-06-19): build/ship NOTHING non-trivial — including bug
  FIXES — without (a) a senior-engineer review AND, for retrieval/answer changes, a RAG/LLM-researcher
  review, AND (b) Mohammad's approval. Flow: design → expert review(s) → he approves → build TDD →
  show the diff → he signs off → commit + restart. Dispatch reviewers as background agents with the
  concrete artifact + file paths; relay findings, don't rubber-stamp. See `feedback_senior_eng_review`.
  - **CHECK DESIGN/BUILD AGAINST THE PLAN (2026-06-20):** the review must verify the work against the
    spec's STATED GOALS — every goal either shipped or **loudly flagged as deferred**, never silently
    dropped. Every spec/PR ends with a goals checklist. See `feedback_review_against_plan`.

## Live feature flags (`.env`, verified 2026-08-22)

ON: `ROUTER_V21=1` (SHADOW=0 → acting) · `ANSWER_GATE_ENABLED=1` · `QUERY_CORRECT_ENABLED=1` ·
`RETRIEVAL_DEEP_FALLBACK=1` (0.30) · `LIVE_ENABLED=1` · `FOLLOWUP_RESUME_ENABLED=1` ·
`ANTECEDENT_GUARD_ENABLED=1` · `PERSON_SCOPE_GUARD_ENABLED=1` · `V2_RETRIEVER_ENABLED=true` ·
`TELEGRAM_ENABLED=true` · `GROUPME_ENABLED=true` · `DASHBOARD_SERVER_ENABLED=true`.
OFF / unset (code defaults apply): `FOOTBALL_ENABLED=false` · `LIVE_OPTIN` · `LIVE_RELEVANCE_GATE` ·
`ROUTER_V21_SLOT_RECOVERY` (defined but read nowhere in production) · `PERSON_ADDENDUM_ENABLED` (RETIRED).
**Most of these default OFF in `bot/config.py` and are ON only because `.env` sets them** — describe
them as "enabled in this deployment", not as code defaults. Never quote `.env` values: it holds live
Discord/Telegram/GroupMe tokens and Brave keys.

## Common Tasks

### Add an RGO / club + officers (manual, `created_by='dashboard'`)
Dashboard: People tab → **+ New club/org** → add officers via the Add form → if you added an
About/bio, run the embed. Backend equivalent (gated): `ensure_org(slug=<acronym>, name,
parent_slug='gsa', type='club')` + `people_editor.add_or_edit_person(...)` per officer + an
"About <club>" `knowledge_item`, then embed. Verify: `who are the <X> officers` / `what is <X>`.

### Add / edit KB prose
Dashboard KB tab (or drop a `.md` in `bot/data/sources/gsa/` → `scripts/gsa_ingest_docs.py --commit`),
then embed.

### Embed new/changed knowledge (TWO passes)
```
python v2/scripts/embed_all.py      # knowledge_vectors (item level)
python v2/scripts/embed_chunks.py   # knowledge_chunks + knowledge_chunk_vectors
```
Both resumable — they only process rows missing a vector.

### (Re)gather crawler people — the recurring refresh
`python scripts/run_explore.py` walks **all 17 roots** in `ALL_ENTRY_POINTS` and refreshes the KG
(people/roles/research-areas) + crawler KB. Re-run whenever NJIT pages change; M3 reconciles turnover
automatically. **This script writes LIVE by default and has no `--commit`** — pass `--backup`, or work
on a dev copy first. `--reset` re-derives crawler data from scratch (manual/dashboard untouched).

**⚠️ NEVER `cp gsa_gateway.db` to make a dev copy.** The DB runs in **WAL mode**, so recent writes live
in `gsa_gateway.db-wal` and a plain `cp` **silently loses them** — the copy is a stale snapshot and
every check against it is quietly wrong. (Caught when a dev copy taken minutes after a committed roster
change still showed the OLD president.) Use the online-backup API, which checkpoints correctly:
```
sqlite3 gsa_gateway.db ".backup /tmp/dev.db"     # or python: src.backup(dst)
python scripts/run_explore.py --db /tmp/dev.db
```
`hardened_backup()` already uses `src.backup(d)` and is WAL-safe — backups were never affected.

**⚠️ ALWAYS run `python scripts/ingest_college_leadership.py --commit` AFTER a crawl.** NJIT
restructured profile pages (2026-08) so a heading carries only the FACULTY RANK while the decanal role
lives in a college-site section heading or About-Me prose. explore.py reads per-person titles, so a
crawl drops those titles (the 2026-08-15 run dropped 33). Most were genuine turnover and correctly
dropped; the still-in-post ones are merged back by that script, verified against `njit_orgchart.pdf`.
`project_appointment` overwrites titles on the first touch of a run by design, so this is a re-runnable
merge-back, NOT a permanent overlay.

### Crawl NJIT prose → KB (three owners, no overlap)
- `python scripts/crawl_www.py` → `njit_www_crawl`, sitemap-driven over 47 www.njit.edu subsites.
  **Owns most college/dept prose today.**
- `python scripts/crawl_college.py [--entry <slug>] [--commit] [--embed]` → `college_crawl`, DFS over
  a college subdomain; each entry independently recrawlable.
- `python scripts/crawl_catalog.py` → `catalog_crawl`, course/program records.
The old generic njit-web pipeline (`_crawl_stage.py` / `_crawl_ingest.py` / `crawl_njit_section.py` /
`ingest_offices.py`) and the dashboard "NJIT office refresh" job were RETIRED 2026-06-25 (they used the
staging/review model the hard line rejects). The per-office `*_crawl.py` runners still exist but
contribute **zero** active rows — `eos_crawl.py` survives only as a LIBRARY (its `extract_prose` /
`_main_region` helpers are imported by the three live crawlers). The live fallback covers the long tail.

### Sync FacultyFolio links
`python scripts/sync_facultyfolio_links.py [--commit]` — re-runnable sync of
`attrs.profiles.facultyfolio.url` for every ACTIVE person against
`https://facultyfolio.github.io/njit/sitemap.xml`: adds missing, fixes stale, and REMOVES a link whose
page left the sitemap. Gated, idempotent, active-people only. **Re-run whenever the folio roster
changes.** (The site went multi-university 2026-08-02: pages are `/njit/p/<slug>.html`, slug = the NJIT
profile slug. Old `/p/<slug>.html` URLs are gone with no redirects.)

### Add a person's external profile / refresh Scholar
Given a name + URLs: find the person key (`SELECT key FROM nodes WHERE type='Person' AND name LIKE …`),
WebFetch a Scholar page for citations/h-index/i10, then `people_editor.set_person_profiles(...)`
(deep-merges; metric strings coerced to numbers) — `hardened_backup` first. DB-only → no restart.
Recurring jobs (all gated, dry-run default), also on the dashboard **Data Sources** tab:
- `scripts/refresh_scholar.py --org X --older-than N --commit --embed` — re-pull metrics + interests→areas
  for people who ALREADY have a Scholar URL.
- `scripts/discover_scholar.py` — for faculty WITHOUT a URL: Brave-search, verify, and auto-write ONLY a
  verified-`njit.edu`-email + name-match + (unique-surname OR corroboration) match
  (`scholar_discovery.classify_candidate` is the SOLE boundary; homonyms → review CSV, never guessed).
- `scripts/discover_scholar_sweep.py --budget N --commit` — slow-drip (~50/hr), block-aware, SIGTERM-safe.
Non-strict outcomes write `scholar.discovery_attempted` so neither job re-searches a dead end.
Scholar blocks bots at volume → discovery is best-effort.

### Refresh research funding (NSF + NIH)
`python scripts/funding_enrich.py --org <slug> [--commit]` — gated, dry-run default, additive only:
writes `attrs.funding.nsf` / `attrs.funding.nih` (+ `attrs.email_aliases`) on Person nodes.

### Restart the bots
`bash scripts/restart.sh` (stops + restarts Discord + Telegram + GroupMe, manages Ollama, relaunches
and verifies the dashboard; kills duplicates first; `--no-llm` to run without Ollama). DB-only changes
need no restart (bots read live); code changes do. Discord re-syncs slash commands on startup.
Ops one-liners: `bash scripts/llm.sh {on|off|status}` (toggle Ollama without touching the bots —
structured/SQL answers keep full quality with it off) · `bash scripts/health_check.sh [--fix]` ·
`bash scripts/stats.sh [--today|--week|--platform X]`.

### Add a structured-retrieval skill (a 4-file change)
`skills.py` (or `entity.py`) → wire into `structured_answer.run/format_answer` → add the rule in
`router.py` (the deterministic resolver) → and, if it has a high-precision cue, add it to
`unified_router._FASTPATH_CUE` so it skips the classifier encode. Add it to `_DETERMINISTIC_SKILLS`
if its output must never be reworded. See `officers_in_org` / `people_in_org` as the pattern.

### Evaluate the bot (coverage + accuracy)
`bash scripts/eval.sh` — runs `eval/questions.txt` (336 Qs / 79 categories) through the REAL pipeline,
classifies each kb/live/deflect, auto-judges accuracy with a local model, removes its own analytics
rows, and prints coverage + accuracy + the gap list. `--limit N` for a subset;
`--min-answered 90 --min-correct 80` makes it a REGRESSION GATE (non-zero exit).
Long-running harness: `bash scripts/autoeval.sh {run|smoke|start|stop}`.
**Every change adds its verification questions to `eval/questions.txt`.**

### Debug a single query (pipeline X-ray)
`bash scripts/ask.sh "<question>" [--verbose] [--answer]` (a wrapper around `scripts/trace_query.py`)
— shows the UnifiedRouter decision, the fused RRF pool, CE rerank scores, the tier verdict + gate
decision, final top-5, and (verbose) the exact LLM prompt / (answer) the real answer.

### Run / test the judging system
Full manual: `docs/judging_system.md`. Dashboard → Judging tab (server mode). Create event → load CSV
→ add judges → Open. Telegram flows: `judge mode` (PIN → score), `presenter mode`, `audience mode`
(anyone votes once). Audience voting is opened/closed independently on the dashboard.
Tables (**six**, in `gsa_gateway_ops.db`): `judging_events`, `judging_judges`, `judging_presenters`,
`judging_scores`, `judging_audience_votes`, `judging_score_audit`.
Tests: `.venv/bin/python -m pytest v2/tests/test_judging_db.py v2/tests/test_judging_calculator.py
v2/tests/test_judging_session.py v2/tests/test_judging_mode_projection.py -q` (108 tests).
⚠️ In-progress judging sessions are IN-MEMORY and do not survive a restart; committed scores are in DB.

## Known gaps worth remembering
- **News coverage is thin**: 88 `news` rows, mostly listing pages, only 1 with a publication date.
  `news.njit.edu` (3,400+ sitemap URLs) is NOT crawled — news questions are carried by the live tier.
- **Almost nothing is scheduled.** ONE cron entry exists as of 2026-08-27:
  `0 5 * * * scripts/daily_restart.sh` (flock-guarded) — bounces the three bots + dashboard and
  unloads the resident Ollama model via the API, logging bots-vs-llama RSS separately to
  `logs/daily_restart.log` so real memory growth is measurable. Everything else — every crawl,
  refresh and embed — is still operator-run.
- **The WorldCup watcher is polling a dead 403 endpoint** — set `V2_WORLDCUP_ENABLED=false`.
- **Dashboard "Organization" tab is a permanently-empty stub.**
- `scripts/crawl_catalog.py`'s usage block still advises `cp gsa_gateway.db /tmp/dev.db` — wrong per
  the WAL rule above; use `.backup`.

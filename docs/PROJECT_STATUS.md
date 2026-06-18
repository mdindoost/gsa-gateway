# GSA Gateway — Project Status & Handover

**Updated:** 2026-06-15

This is the "where are we" doc for anyone (human or a fresh AI agent) picking up the
project. Read this + `CLAUDE.md` + the code + `git log`. **Note:** prior sessions' private
agent memory does NOT travel between machines/tools — this repo is the source of truth.

---

## Current state (one paragraph)

GSA Gateway is a v2 system: a Discord + Telegram **all-conversational** assistant (only
`/qrcode` is a slash command) backed by a single SQLite DB (`gsa_gateway.db`) with a **text
layer** (`knowledge_items` + sqlite-vec) and a **graph layer** (`nodes`/`edges`: people,
orgs, research areas, roles). A hybrid retriever (semantic + FTS, RRF) plus a deterministic
structured router answer questions; Ollama `llama3.1:8b` generates grounded prose. **YWCC/
NJIT** knowledge is gathered by the `explore()` crawler; **GSA + its clubs/RGOs** are
maintained **manually** (gsanjit.com is Wix, not crawlable) via the dashboard People & Roles
editor. A dashboard (`v2/local_server.py` + `dashboard/`) is the admin control plane.

## Shipped & live

- **v1 → v2 cut**: ChromaDB/rapidfuzz/`/ask…` removed; v2 = sqlite-vec + KG + dashboard.
- **Knowledge graph + crawler**: `explore()` gathers YWCC people/roles/orgs/research-areas;
  M3 reconcile handles departures/moves; `verify_kg` alignment gate.
- **Hybrid + structured retrieval**: `V2Retriever` (semantic+FTS+RRF); router→skills→
  structured_answer for enumerate/filter/traverse/count. Publications/webpages excluded from
  the default answer corpus; `contact` boost removed.
- **GSA KG+KB (manual)**: GSA officers in the graph; policy/program prose in the KB;
  legacy GSA Q&A retired. President (Teik C. Lim) + deans modeled.
- **All-conversational bots**: `/qrcode` restored (Discord + Telegram, shared `bot/services/
  qr.py`); `/contact` + `/help` removed; Telegram trimmed to `/start` + `/qrcode`.
- **People & Roles dashboard editor (Spec A)**: add/edit/soft-remove people + arbitrary
  free-text roles (+ optional embedded bio) for any org; `people_in_org` skill + route so
  all role types answer; `POST /people` + `/people/remove`.
- **Org resolver**: resolves orgs by name / slug / parenthetical acronym / `metadata.aliases`;
  org-create modal has an editable Slug field (defaults to the acronym).
- **Clubs/RGOs created so far** (all under GSA, `source='dashboard'`): GWICS, BGSA (no
  officers yet), GBMES, ICA (Iranian Cultural Assoc.), Sanskar (Indian Cultural Assoc.).

## Deferred / queued (not built)

- **Spec B — multi-org crawler + dashboard "pick orgs to re-crawl" control page**
  (e.g. Graduate Studies on njit.edu, an org→entry-point registry, per-page extractor).
  Designed-in-conversation, not specced/built. The biggest queued piece.
- **Conversational actions** — submit an initiative / send feedback via chat (intent→action
  routing). Features exist in spirit; the chat-trigger layer isn't built.
- **MMI** — still hand-written Q&A; a proper migration needs a new Event/Talk ontology.
- **Phase 2 LLM extraction** (awards/advising/affiliation/publications as graph nodes) —
  **dropped**: a probe showed the text layer suffices and the small model is unreliable for
  it. Don't resurrect without re-running the evidence.

## Abandoned / superseded (don't follow these)

- **Crawling gsanjit.com for GSA** — abandoned; gsanjit.com is Wix/React, rosters aren't in
  parseable HTML (see `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md`). GSA is
  **manual**. The spec `2026-06-15-gsa-website-kg-kb-crawl-design.md` is **SUPERSEDED** by
  the manual plan `2026-06-15-gsa-kg-kb-foundation.md`.
- **`contact` retrieval boost** — removed (it over-ranked campus-office cards).

## Known small gaps / follow-ups

- Dashboard **"+ New club/org"** quick-add (People tab) still creates a *parentless* org;
  use the **KB tab → Add Organization** modal (with the Slug + Parent fields) for clubs that
  need to sit under GSA.
- No dashboard UI yet to set `organizations.metadata.aliases` (the resolver reads it; set it
  via the org modal's metadata or backend).
- Editing **crawler-owned** people in the dashboard is intentionally out of scope (the
  crawler owns `source='crawler'` rows).

## Key workflows (quick reference)

- **Add a club + officers** → dashboard People tab, or backend `ensure_org` +
  `people_editor.add_or_edit_person` + an "About" `knowledge_item`, then `embed_all.py`.
- **Embed** new/changed knowledge → `python v2/scripts/embed_all.py` (resumable).
- **Gather YWCC** → `python scripts/run_explore.py --commit` then `embed_all.py` then
  `scripts/verify_kg.py`.
- **Gated live write** → scripts take a `hardened_backup`, default dry-run, need `--commit`.
- **Restart bots** → `bash scripts/restart.sh`. DB changes need no restart; code changes do.

## Design-doc index (`docs/superpowers/`)

Status legend: **SHIPPED** (built & live) · **HISTORICAL** (one-time, done) ·
**SUPERSEDED** (replaced — don't follow) · **DEFERRED** (not built).

| Doc | Status |
|---|---|
| specs/2026-06-04-multi-platform-connectors-design.md | SHIPPED (Discord+Telegram connectors) |
| specs/2026-06-08-free-mode-and-identity-design.md | SHIPPED |
| specs/2026-06-10-v2-platform-architecture-design.md | **Foundational** — the v2 architecture |
| specs/2026-06-11-hybrid-knowledge-ingestion.md | SHIPPED (ingestion engine) |
| specs/2026-06-12-dashboard-control-plane.md | SHIPPED (Jobs tab) |
| specs/2026-06-13-ds-crawler-design.md | SUPERSEDED by the kg-gathering-engine |
| specs/2026-06-13-overview-review-qa.md | SHIPPED |
| specs/2026-06-13-refresh-njit-kb-design.md | SHIPPED (refresh job) |
| specs/2026-06-13-structured-retrieval-phase1.md | SHIPPED (router/skills) |
| specs/2026-06-14-kg-gathering-engine-design.md | SHIPPED (explore crawler) |
| specs/2026-06-14-people-roles-kg-ingestion-design.md | SHIPPED (crawler people/roles) |
| specs/2026-06-14-research-area-facet-design.md | SHIPPED |
| specs/2026-06-14-research-pane-extraction-fix.md | SHIPPED |
| specs/2026-06-14-semantic-area-matching.md | SHIPPED (area synonyms) |
| specs/2026-06-15-gsa-website-kg-kb-crawl-design.md | **SUPERSEDED** → manual (see finding) |
| specs/2026-06-15-people-roles-dashboard-editor-design.md | SHIPPED (Spec A) |
| plans/2026-06-04-multi-platform-connectors.md | HISTORICAL |
| plans/2026-06-08-free-mode-and-identity.md | HISTORICAL |
| plans/2026-06-10-generator-post-sources.md | HISTORICAL |
| plans/2026-06-10-phase0-v1-v2-cut.md | HISTORICAL (the v1→v2 cut) |
| plans/2026-06-14-phase1a-graph-foundation.md | SHIPPED |
| plans/2026-06-14-research-area-facet.md | SHIPPED |
| plans/2026-06-14-retrieval-facet-followups.md | SHIPPED |
| plans/2026-06-15-gsa-kg-kb-foundation.md | SHIPPED (GSA manual KG+KB) |
| plans/2026-06-15-people-roles-dashboard-editor.md | SHIPPED (Spec A) |
| findings/2026-06-15-gsa-wix-extraction.md | DECISION: GSA = MANUAL |

## To run it (handover checklist)

1. Clone/pull `origin/main`.
2. `.env` with bot tokens (Discord, Telegram) + Ollama running (`nomic-embed-text`,
   `llama3.1:8b`).
3. `pip install -r requirements.txt` (incl. `sqlite-vec`, `tiktoken`, `pyyaml`, `discord.py`,
   `python-telegram-bot`, `qrcode`, `Pillow`).
4. `bash scripts/restart.sh` (bots) — the dashboard server is launched as a child; reach it
   via an SSH tunnel to `localhost:5555`.
5. Tests: `python -m pytest v2/tests/ -q` (note: ~7 pre-existing `local_server` auth /
   departments failures are unrelated and predate current work).

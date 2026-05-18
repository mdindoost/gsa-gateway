# Architecture — GSA Gateway

## Overview

```
                     Discord API
                         │
                    discord.py 2.x
                         │
                    ┌────┴────┐
                    │ GSABot  │   (bot/main.py)
                    └────┬────┘
          ┌──────────────┼──────────────────┐
          │              │                  │
     KnowledgeBase   Database          RateLimiter
     (data files)    (SQLite)          (in-memory)
          │              │
     SearchService   SummaryService
     (rapidfuzz)
          │
    (optional)
    OllamaClient
    (local LLM)
```

## Component Responsibilities

### bot/main.py — GSABot
- Creates the `commands.Bot` subclass
- `setup_hook()` initialises all services and loads cog extensions
- Syncs slash commands to the configured guild (or globally)
- Handles top-level `on_app_command_error`

### bot/config.py — Config
- Reads environment variables via `python-dotenv`
- Single global `config` object imported by commands and services
- No hardcoded values — everything is configurable via `.env`

### bot/services/knowledge_base.py — KnowledgeBase
- Loads all data files at startup into typed dataclass collections
- `gsa_faq.md` → list of `FAQEntry` (regex parser)
- `events.yml` → list of `Event`
- `contacts.yml` → dict of `Contact`
- `resources.yml` → dict of `list[Resource]`
- `get_searchable_texts()` returns a flat list for the search service

### bot/services/search.py — SearchService
- Uses `rapidfuzz.process.extract` with `fuzz.token_set_ratio`
- Default min_confidence = 60.0 (configurable per-instance)
- `search(query)` → FAQ results
- `search_events(name)` → event results  
- `search_contacts(role)` → single best match

### bot/services/database.py — Database
- Thin wrapper around `sqlite3`
- All user IDs are SHA-256 hashed before storage (`hash_user_id()`)
- 5 tables: questions, initiatives, feedback, events_log, admin_actions
- WAL journal mode for concurrency-safe reads during bot operation

### bot/services/moderation.py
- `RateLimiter`: sliding-window, in-memory, per-user (max 5/min)
- `is_channel_allowed()`: checks channel name against `ALLOWED_CHANNELS`
- `is_admin()`: checks Discord role name against `ADMIN_ROLE_NAME`

### bot/services/ollama_client.py — OllamaClient
- Optional; only instantiated when `OLLAMA_ENABLED=true`
- Calls `POST /api/generate` with a RAG-style prompt
- **Always requires retrieved context** — cannot answer without KB excerpts
- Times out after 30s; returns `None` on any failure (bot falls back to direct answer)

### bot/services/summaries.py — SummaryService
- Queries DB for recent initiatives and feedback
- Renders markdown-formatted summary text for `/admin_summary` and the export script

## Data Flow — /ask Command

```
User types /ask "how do I get funding?"
    │
    ▼
Rate limit check → denied? → ephemeral error
    │
    ▼
Channel allowlist check → denied? → ephemeral error
    │
    ▼
search_svc.search(question, limit=3)
    │
    ├── score < 60 for best match?
    │       └── log to DB (confidence=low) → send fallback message
    │
    └── score ≥ 60
            │
            ├── OLLAMA_ENABLED=true? → pass context to OllamaClient
            │                          use enhanced answer if successful
            │
            └── Build embed: question, answer, footer (source + confidence %)
                    → send embed (public)
                    → log to DB (matched_topic, confidence)
```

## Privacy Model

| Data | How stored |
|---|---|
| Discord user ID | SHA-256 hash only |
| Question text | Plain text (no linkage to user) |
| Initiative content | Plain text; contact info only if `include_contact=True` |
| Feedback message | Plain text; no user linkage |
| Admin exports | All exports omit user identifiers |

The SHA-256 hash is one-way and salted implicitly by the 64-character Discord ID space. A future version could add a server-side secret as a HMAC key for stronger separation.

## Website Architecture

Static HTML/CSS/JS with zero build steps.
- `events.html` fetches `data/events.json` via `fetch()` at page load
- `app.js` handles HTML escaping, loading state, and error fallback
- No frameworks, no dependencies — GitHub Pages compatible out of the box
- CSS uses custom properties for the NJIT color palette

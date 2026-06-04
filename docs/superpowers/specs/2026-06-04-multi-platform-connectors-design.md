# Multi-Platform Connector Architecture — Design Spec
**Date:** 2026-06-04  
**Status:** Approved  
**Scope:** Add Telegram support (core Q&A) and establish an extensible connector pattern for future platforms (WhatsApp, etc.)

---

## Problem

The bot's intelligence lives entirely in `bot/services/` and is already platform-agnostic. However, `bot/commands/chat.py` mixes Discord-specific code (embeds, `message.reply`, typing indicators) with business logic (intent detection, RAG retrieval, Ollama generation). Adding Telegram requires extracting that logic so both platforms can share it.

---

## Goals

1. Telegram users can ask natural language questions answered by the RAG pipeline.
2. Telegram supports `/events`, `/contact`, `/resources` commands.
3. Adding a third platform (e.g. WhatsApp) requires implementing one ABC and writing one entry point — no changes to services or existing connectors.
4. Each platform runs as its own independent process.

---

## Non-Goals

- Telegram admin commands (no role system equivalent).
- Telegram `/feedback` or `/initiative` (no modal equivalent — out of scope for now).
- Single-process multi-platform runtime.
- WhatsApp implementation (only the pattern is established here).

---

## Architecture

### Directory Structure

```
gsa-gateway/
├── bot/
│   ├── core/
│   │   └── message_handler.py       ← platform-agnostic brain (extracted from chat.py)
│   ├── connectors/
│   │   ├── base.py                  ← BasePlatform ABC
│   │   ├── discord_connector.py     ← thin wrapper; chat.py delegates to MessageHandler
│   │   └── telegram_connector.py   ← new Telegram connector
│   ├── commands/                    ← unchanged (Discord slash commands)
│   ├── services/                    ← unchanged (RAG, DB, KB, etc.)
│   └── main.py                      ← unchanged (Discord entry point)
├── run_telegram.py                  ← new Telegram entry point
└── .env                             ← adds TELEGRAM_TOKEN
```

### Deployment Model

Two independent processes share the same codebase but not runtime state:

```
process 1: python bot/main.py        → Discord bot
process 2: python run_telegram.py    → Telegram bot
```

Each process initializes its own copy of DB, KB, RAG services, rate limiter, and conversation manager. Conversation history is per-process and per-platform (intentional — a Discord session and a Telegram session for the same person are independent).

---

## Components

### `BasePlatform` ABC (`bot/connectors/base.py`)

Contract every connector must implement:

```python
class BasePlatform(ABC):
    @abstractmethod
    async def start(self) -> None: ...       # begin polling / connect to platform API

    @abstractmethod
    async def stop(self) -> None: ...        # graceful shutdown

    @abstractmethod
    async def setup_services(self) -> None:  # init DB, KB, RAG, rate limiter, etc.
```

Adding a new platform = subclass `BasePlatform`, implement these 3 methods, write `run_<platform>.py`.

---

### `MessageHandler` (`bot/core/message_handler.py`)

Platform-agnostic request/response boundary. Receives a plain request, returns a plain response. No Discord or Telegram objects cross this boundary.

```python
@dataclass
class MessageRequest:
    user_id: str      # platform-agnostic string ID
    text: str
    platform: str     # "discord" | "telegram" | "whatsapp"

@dataclass
class MessageResponse:
    text: str
    source_note: str | None   # e.g. "GSA FAQ" — connector decides how to display it
    used_ai: bool

class MessageHandler:
    def __init__(
        self,
        retriever, ollama, conversation_manager,
        intent_detector, db, rate_limiter, kb, config,
    ): ...

    async def handle(self, req: MessageRequest) -> MessageResponse: ...
```

`handle()` contains the logic currently in `chat.py` steps 1–7:
1. Get conversation history
2. Expand short queries
3. Detect intent
4. Retrieve chunks (RAG)
5. Generate answer (Ollama or fallback)
6. Update conversation memory
7. Log interaction to DB

Rate limiting is enforced inside `handle()` — returns a `MessageResponse` with a rate-limit message rather than raising.

---

### `TelegramConnector` (`bot/connectors/telegram_connector.py`)

Library: `python-telegram-bot` v20+ (async, matches our asyncio stack).

**Free-form Q&A:** All text messages routed through `MessageHandler.handle()`. Response sent as Markdown text (Telegram supports `*bold*`, `_italic_`, no Discord embeds).

**Commands:**
- `/events` — formats `kb.events` as a text list with dates and descriptions.
- `/contact [role]` — looks up `kb.contacts`, returns matching contact or full list.
- `/resources [category]` — lists `kb.resources` for the given category or all categories.
- `/start` / `/help` — introduction message explaining what the bot can do.

**Source attribution:** Appended as `_Source: GSA FAQ_` in italic below the response text.

---

### `discord_connector.py` (`bot/connectors/discord_connector.py`)

A thin wrapper for symmetry — `ChatCog.on_message` delegates to `MessageHandler.handle()` instead of doing the logic inline. Discord-specific code (embed building, `message.reply`, typing indicator, admin DM alert) stays in the cog.

---

### Config changes (`bot/config.py`)

One new field:

```python
telegram_token: str   # from env TELEGRAM_TOKEN
```

`.env.example` updated with `TELEGRAM_TOKEN=` (empty default — Telegram disabled if not set).

---

## Error Handling

- `MessageHandler.handle()` catches all internal exceptions and returns a fallback `MessageResponse` with a "contact a GSA officer" message. No exception escapes to the connector.
- If Ollama is unreachable, degrades to raw KB text (same as Discord today).
- Ollama-down admin DM alert: Discord connector keeps this behavior. Telegram connector logs only (no Telegram equivalent of `admin_discord_id`).
- Telegram network errors (polling failures) are handled by `python-telegram-bot`'s built-in retry logic.
- A crash in the Telegram process has no effect on the Discord process.

---

## Testing

| Test file | What it covers |
|---|---|
| `bot/tests/test_message_handler.py` | Intent routing, RAG fallback, rate limit response, conversation history passthrough — no Discord/Telegram mocks needed |
| `bot/tests/test_telegram_connector.py` | Command handlers, message formatting, Markdown rendering — uses `python-telegram-bot` test utilities |
| Existing `bot/tests/` | Unchanged — services tests continue to pass |

---

## Adding a Future Platform (e.g. WhatsApp)

1. `pip install <whatsapp-library>`
2. Create `bot/connectors/whatsapp_connector.py` — subclass `BasePlatform`, implement `setup_services()`, `start()`, `stop()`.
3. In the message handler: call `MessageHandler.handle(MessageRequest(user_id=..., text=..., platform="whatsapp"))`.
4. Write `run_whatsapp.py` entry point (copy `run_telegram.py`, swap connector class).
5. Add `WHATSAPP_TOKEN` to `.env` and `Config`.

No changes to `bot/services/`, `bot/commands/`, or existing connectors.

---

## File Change Summary

| File | Change |
|---|---|
| `bot/core/message_handler.py` | New — logic extracted from `chat.py` |
| `bot/connectors/base.py` | New — `BasePlatform` ABC |
| `bot/connectors/telegram_connector.py` | New |
| `bot/connectors/discord_connector.py` | New thin wrapper |
| `bot/commands/chat.py` | Refactored — delegates to `MessageHandler` |
| `bot/config.py` | Adds `telegram_token` field |
| `run_telegram.py` | New entry point |
| `bot/services/` | **Untouched** |
| `bot/commands/` (non-chat) | **Untouched** |

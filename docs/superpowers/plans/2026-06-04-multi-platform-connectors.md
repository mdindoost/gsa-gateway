# Multi-Platform Connector Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the bot's RAG logic into a platform-agnostic `MessageHandler`, wire up a `BasePlatform` ABC, and ship a working Telegram connector with Q&A + `/events`, `/contact`, `/resources`.

**Architecture:** `bot/core/message_handler.py` holds all intent/RAG/Ollama logic lifted from `chat.py`. `bot/connectors/base.py` defines `BasePlatform`. `TelegramConnector` implements it. Discord's `ChatCog` is refactored to delegate to `MessageHandler`. Each platform runs as its own independent process.

**Tech Stack:** Python 3.11+, discord.py 2.x (existing), python-telegram-bot 20.x (new), asyncio, pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add `python-telegram-bot>=20.0` |
| `bot/services/food_detector.py` | Modify | Add `format_food_text()` — text-based food formatter |
| `bot/core/__init__.py` | Create | Package stub |
| `bot/core/message_handler.py` | Create | Platform-agnostic brain: `MessageRequest`, `MessageResponse`, `MessageHandler` |
| `bot/connectors/__init__.py` | Create | Package stub |
| `bot/connectors/base.py` | Create | `BasePlatform` ABC |
| `bot/connectors/telegram_connector.py` | Create | Telegram connector: Q&A + 4 commands |
| `bot/commands/chat.py` | Modify | Delegate to `MessageHandler`; remove inline RAG logic |
| `bot/main.py` | Modify | Instantiate `MessageHandler`, attach to `bot.message_handler` |
| `bot/config.py` | Modify | Add `telegram_token: str` field |
| `run_telegram.py` | Create | Telegram entry point |
| `bot/tests/test_message_handler.py` | Create | Unit tests for `MessageHandler` (no Discord/Telegram mocks) |
| `bot/tests/test_telegram_connector.py` | Create | Unit tests for `TelegramConnector` command handlers |

---

### Task 1: Add dependency and package stubs

**Files:**
- Modify: `requirements.txt`
- Create: `bot/core/__init__.py`
- Create: `bot/connectors/__init__.py`

- [ ] **Step 1: Add python-telegram-bot to requirements**

In `requirements.txt`, add after the last line:
```
python-telegram-bot>=20.0
```

- [ ] **Step 2: Create package stubs**

Create `bot/core/__init__.py` — empty file.
Create `bot/connectors/__init__.py` — empty file.

- [ ] **Step 3: Install dependency**

```bash
pip install python-telegram-bot>=20.0
```

Expected: installs without errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt bot/core/__init__.py bot/connectors/__init__.py
git commit -m "feat: add python-telegram-bot dependency and package stubs"
```

---

### Task 2: Add `format_food_text()` to `food_detector.py`

**Files:**
- Modify: `bot/services/food_detector.py`

The existing `format_food_response()` returns a Discord embed. We need a Markdown-string version the `MessageHandler` can use on any platform.

- [ ] **Step 1: Write the failing test**

In `bot/tests/test_food_detector.py` (already exists — add to it):

```python
from bot.services.food_detector import format_food_text

def test_format_food_text_today_and_upcoming():
    from datetime import date
    today = date.today().isoformat()
    events = [
        {"name": "Pizza Party", "date": today, "time": "5 PM", "location": "CC 110"},
        {"name": "Ice Cream Social", "date": "2099-12-31", "time": "3 PM", "location": "Atrium",
         "description": ""},
    ]
    result = format_food_text(events)
    assert "pizza party" in result.lower()
    assert "ice cream social" in result.lower()
    assert "5 pm" in result.lower()

def test_format_food_text_empty():
    result = format_food_text([])
    assert result == "" or "no" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest bot/tests/test_food_detector.py -k "format_food_text" -v
```

Expected: FAIL — `ImportError: cannot import name 'format_food_text'`

- [ ] **Step 3: Implement `format_food_text` in `food_detector.py`**

Add after `format_food_response()`:

```python
def format_food_text(food_events: list[dict]) -> str:
    """Format food events as plain Markdown text (platform-agnostic)."""
    if not food_events:
        return "No upcoming food events found this week."
    today_str = date.today().isoformat()
    today_events = [e for e in food_events if e["date"] == today_str]
    upcoming_events = [e for e in food_events if e["date"] > today_str]
    lines = []
    if today_events:
        lines.append("**Free Food Today!**\n")
        for ev in today_events[:5]:
            lines.append(f"**{ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            if ev.get("description"):
                lines.append(str(ev["description"])[:120])
            lines.append("")
    if upcoming_events:
        lines.append("**Upcoming Food Events This Week**\n")
        for ev in upcoming_events[:5]:
            try:
                d = date.fromisoformat(ev["date"])
                day_str = f"{d.strftime('%A, %b')} {d.day}"
            except ValueError:
                day_str = ev["date"]
            lines.append(f"**{day_str} — {ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            lines.append("")
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest bot/tests/test_food_detector.py -k "format_food_text" -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest bot/tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add bot/services/food_detector.py bot/tests/test_food_detector.py
git commit -m "feat: add format_food_text() for platform-agnostic food event formatting"
```

---

### Task 3: Create `MessageRequest`, `MessageResponse`, and `MessageHandler` stub

**Files:**
- Create: `bot/core/message_handler.py`
- Create: `bot/tests/test_message_handler.py`

- [ ] **Step 1: Write failing tests**

Create `bot/tests/test_message_handler.py`:

```python
"""Tests for the platform-agnostic MessageHandler."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_QUESTION,
    INTENT_THANKS,
)


@pytest.fixture
def mock_services():
    rate_limiter = MagicMock()
    rate_limiter.is_allowed.return_value = True

    intent_detector = MagicMock()
    intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)

    conversation_manager = MagicMock()
    conversation_manager.get_session.return_value = None
    conversation_manager.get_history.return_value = []

    config = MagicMock()
    config.conversation_max_turns = 5

    return {
        "retriever": AsyncMock(),
        "ollama": None,
        "conversation_manager": conversation_manager,
        "intent_detector": intent_detector,
        "db": MagicMock(),
        "rate_limiter": rate_limiter,
        "kb": MagicMock(),
        "config": config,
    }


@pytest.fixture
def handler(mock_services):
    return MessageHandler(**mock_services)


@pytest.mark.asyncio
async def test_rate_limited_returns_wait_message(handler):
    handler.rate_limiter.is_allowed.return_value = False
    req = MessageRequest(user_id="123", text="hello", platform="discord")
    resp = await handler.handle(req)
    assert "wait" in resp.text.lower() or "too quickly" in resp.text.lower()
    assert resp.source_note is None
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_greeting_no_history_returns_full_intro(handler):
    handler.intent_detector.detect.return_value = (INTENT_GREETING, 0.95)
    handler.conversation_manager.get_session.return_value = None
    req = MessageRequest(user_id="123", text="hi", platform="telegram")
    resp = await handler.handle(req)
    assert "gsa gateway" in resp.text.lower()
    assert "njit" in resp.text.lower()


@pytest.mark.asyncio
async def test_greeting_with_history_returns_short_welcome(handler):
    handler.intent_detector.detect.return_value = (INTENT_GREETING, 0.95)
    session = MagicMock()
    session.turns = [MagicMock(), MagicMock()]
    handler.conversation_manager.get_session.return_value = session
    req = MessageRequest(user_id="123", text="hi again", platform="discord")
    resp = await handler.handle(req)
    assert "welcome back" in resp.text.lower()


@pytest.mark.asyncio
async def test_thanks_returns_acknowledgment(handler):
    handler.intent_detector.detect.return_value = (INTENT_THANKS, 0.9)
    req = MessageRequest(user_id="123", text="thanks!", platform="discord")
    resp = await handler.handle(req)
    assert any(
        word in resp.text.lower()
        for word in ("welcome", "glad", "happy", "help")
    )


@pytest.mark.asyncio
async def test_clear_history_clears_session(handler):
    handler.intent_detector.detect.return_value = (INTENT_CLEAR_HISTORY, 0.9)
    req = MessageRequest(user_id="123", text="clear", platform="discord")
    resp = await handler.handle(req)
    handler.conversation_manager.clear_session.assert_called_once_with("123")
    assert "clear" in resp.text.lower() or "fresh" in resp.text.lower()


@pytest.mark.asyncio
async def test_help_returns_command_list(handler):
    handler.intent_detector.detect.return_value = (INTENT_HELP, 0.9)
    req = MessageRequest(user_id="123", text="help", platform="telegram")
    resp = await handler.handle(req)
    assert "/events" in resp.text or "events" in resp.text.lower()


@pytest.mark.asyncio
async def test_question_no_chunks_returns_fallback(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    handler.retriever.retrieve = AsyncMock(return_value=[])
    req = MessageRequest(user_id="123", text="what is gsa?", platform="discord")
    resp = await handler.handle(req)
    assert "gsa-pres@njit.edu" in resp.text or "contact" in resp.text.lower()
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_question_chunks_no_ollama_returns_chunk_text(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards for grad students."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = None
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert "gsa provides travel awards" in resp.text.lower()
    assert resp.source_note is not None
    assert not resp.used_ai


@pytest.mark.asyncio
async def test_question_with_ollama_returns_ai_response(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = AsyncMock()
    handler.ollama.generate_answer = AsyncMock(
        return_value="GSA provides travel awards for presenting at conferences."
    )
    handler.ollama.expand_query = AsyncMock(return_value=None)
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert resp.text == "GSA provides travel awards for presenting at conferences."
    assert resp.used_ai is True


@pytest.mark.asyncio
async def test_ollama_failure_sets_ollama_failed(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    chunk = MagicMock()
    chunk.text = "GSA provides travel awards."
    chunk.source_file = "gsa_faq.md"
    chunk.section_title = "Travel Awards"
    chunk.relevance_score = 0.85
    handler.retriever.retrieve = AsyncMock(return_value=[chunk])
    handler.ollama = AsyncMock()
    handler.ollama.generate_answer = AsyncMock(return_value=None)  # Ollama down
    handler.ollama.expand_query = AsyncMock(return_value=None)
    req = MessageRequest(user_id="123", text="travel award?", platform="discord")
    resp = await handler.handle(req)
    assert resp.ollama_failed is True
    assert not resp.used_ai
    assert "gsa provides travel awards" in resp.text.lower()


@pytest.mark.asyncio
async def test_logs_question_to_db(handler):
    handler.intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    handler.retriever.retrieve = AsyncMock(return_value=[])
    req = MessageRequest(user_id="999", text="what is gsa?", platform="discord", guild_id=42)
    await handler.handle(req)
    handler.db.log_question.assert_called_once()
    call_kwargs = handler.db.log_question.call_args.kwargs
    assert call_kwargs["guild_id"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest bot/tests/test_message_handler.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core.message_handler'`

- [ ] **Step 3: Create `MessageRequest`, `MessageResponse`, and empty `MessageHandler` stub**

Create `bot/core/message_handler.py`:

```python
"""Platform-agnostic message handler — the shared brain for all connectors."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MessageRequest:
    user_id: str
    text: str
    platform: str               # "discord" | "telegram"
    guild_id: Optional[int] = None


@dataclass
class MessageResponse:
    text: str
    source_note: Optional[str] = None
    used_ai: bool = False
    ollama_failed: bool = False


class MessageHandler:
    def __init__(
        self,
        retriever,
        ollama,
        conversation_manager,
        intent_detector,
        db,
        rate_limiter,
        kb,
        config,
    ) -> None:
        self.retriever = retriever
        self.ollama = ollama
        self.conversation_manager = conversation_manager
        self.intent_detector = intent_detector
        self.db = db
        self.rate_limiter = rate_limiter
        self.kb = kb
        self.config = config

    async def handle(self, req: MessageRequest) -> MessageResponse:
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify the import works but tests still fail**

```bash
pytest bot/tests/test_message_handler.py -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 5: Commit the stub**

```bash
git add bot/core/message_handler.py bot/tests/test_message_handler.py
git commit -m "feat: add MessageRequest/MessageResponse/MessageHandler stub with tests"
```

---

### Task 4: Implement `MessageHandler.handle()` — non-RAG branches

**Files:**
- Modify: `bot/core/message_handler.py`

- [ ] **Step 1: Implement all non-RAG intent branches**

Replace the `handle()` body in `bot/core/message_handler.py` with the full implementation. The complete file content:

```python
"""Platform-agnostic message handler — the shared brain for all connectors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from bot.services.food_detector import format_food_text, get_food_events
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FOOD,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_QUESTION,
    INTENT_SOCIAL,
    INTENT_THANKS,
)
from bot.services.retriever import SOURCE_FRIENDLY_NAMES

logger = logging.getLogger(__name__)

_OFFICER_FIRST_NAMES = {
    "fernando", "mohammad", "mohith", "durvish", "nistha", "ritwik",
}


@dataclass
class MessageRequest:
    user_id: str
    text: str
    platform: str               # "discord" | "telegram"
    guild_id: Optional[int] = None


@dataclass
class MessageResponse:
    text: str
    source_note: Optional[str] = None
    used_ai: bool = False
    ollama_failed: bool = False


class MessageHandler:
    def __init__(
        self,
        retriever,
        ollama,
        conversation_manager,
        intent_detector,
        db,
        rate_limiter,
        kb,
        config,
    ) -> None:
        self.retriever = retriever
        self.ollama = ollama
        self.conversation_manager = conversation_manager
        self.intent_detector = intent_detector
        self.db = db
        self.rate_limiter = rate_limiter
        self.kb = kb
        self.config = config

    async def handle(self, req: MessageRequest) -> MessageResponse:
        user_id = req.user_id

        # Rate limiting
        if self.rate_limiter and not self.rate_limiter.is_allowed(user_id):
            remaining = getattr(self.rate_limiter, "get_retry_after", lambda _: 30)(user_id)
            return MessageResponse(
                text=f"You're sending messages too quickly. Please wait {int(remaining)} seconds."
            )

        clean_text = req.text.strip()
        if not clean_text:
            return MessageResponse(text="")

        # Detect intent
        if self.intent_detector:
            intent, _ = self.intent_detector.detect(clean_text)
        else:
            intent = INTENT_QUESTION

        # ── Non-RAG intents ──────────────────────────────────────────────────

        if intent == INTENT_CLEAR_HISTORY:
            if self.conversation_manager:
                self.conversation_manager.clear_session(user_id)
            return MessageResponse(
                text="Conversation cleared! Starting fresh. What would you like to know about GSA?"
            )

        if intent == INTENT_GREETING:
            session = (
                self.conversation_manager.get_session(user_id)
                if self.conversation_manager
                else None
            )
            if session and len(session.turns) > 0:
                text = (
                    "Welcome back! What else can I help you with?\n"
                    "_(Type 'clear' to start a new conversation)_"
                )
            else:
                text = (
                    "Hi! I'm *GSA Gateway*, NJIT's Graduate Student Association assistant.\n\n"
                    "I can help you with:\n"
                    "- GSA events and announcements\n"
                    "- Travel awards and funding\n"
                    "- Club financial rules\n"
                    "- Officer contacts\n"
                    "- GSA constitution and policies\n"
                    "- Campus resources\n\n"
                    "Just ask me anything!"
                )
            return MessageResponse(text=text)

        if intent == INTENT_THANKS:
            return MessageResponse(
                text="You're welcome! Let me know if you have more questions about GSA."
            )

        if intent == INTENT_HELP:
            return MessageResponse(
                text=(
                    "Here's how to use GSA Gateway:\n\n"
                    "Just type your question naturally!\n\n"
                    "*Commands:*\n"
                    "- /events — see upcoming events\n"
                    "- /contact [role] — find GSA contacts\n"
                    "- /resources [category] — campus resources\n\n"
                    "*Tips:*\n"
                    "- Ask follow-up questions naturally\n"
                    "- Type 'clear' to reset our conversation"
                )
            )

        # ── RAG pipeline ──────────────────────────────────────────────────────
        return await self._rag_pipeline(req, clean_text, intent)

    async def _rag_pipeline(
        self, req: MessageRequest, clean_text: str, intent: str
    ) -> MessageResponse:
        user_id = req.user_id
        try:
            chunks = []
            response_text = ""
            source_note = None
            used_ai = False
            ollama_failed = False

            # Conversation history
            history: list[dict] = []
            if self.conversation_manager:
                max_turns = getattr(self.config, "conversation_max_turns", 5)
                history = self.conversation_manager.get_history(user_id, max_turns=max_turns)

            # Expand short/officer queries
            words = clean_text.split()
            core = clean_text.strip("?!.,").strip().lower()
            is_officer_query = any(
                name in core.split() or core == name for name in _OFFICER_FIRST_NAMES
            )
            search_query = clean_text
            contact_filter = None

            if is_officer_query:
                search_query = (
                    f"Who is {core.split()[0].title()} at GSA NJIT? "
                    f"Contact information and role for {core.split()[0].title()}"
                )
                contact_filter = "contact"
            elif self.ollama and len(words) <= 3 and intent not in (INTENT_FOOD, INTENT_SOCIAL):
                expanded = await self.ollama.expand_query(clean_text)
                if expanded and expanded.lower() != clean_text.lower():
                    search_query = expanded

            # Retrieve
            if intent == INTENT_FOOD:
                if self.retriever:
                    chunks = await self.retriever.retrieve_for_food_query()
                food_events = get_food_events(kb=self.kb, db=self.db, days_ahead=7)
                if food_events:
                    if self.conversation_manager:
                        self.conversation_manager.add_turn(
                            user_id=user_id, role="user", content=clean_text
                        )
                        self.conversation_manager.add_turn(
                            user_id=user_id,
                            role="assistant",
                            content="[Food events listed]",
                            source_files=["events.yml"],
                        )
                    if self.db:
                        self.db.log_question(
                            user_id=int(user_id),
                            question=clean_text,
                            matched_topic="food events",
                            confidence=100.0,
                            guild_id=req.guild_id,
                        )
                    return MessageResponse(
                        text=format_food_text(food_events),
                        source_note="GSA Events",
                    )
            elif intent == INTENT_SOCIAL:
                if self.retriever:
                    chunks = await self.retriever.retrieve(
                        query="social events activities networking happy hour graduate students",
                        source_type_filter="event",
                    )
            elif self.retriever:
                chunks = await self.retriever.retrieve(
                    query=search_query,
                    conversation_history=history,
                    source_type_filter=contact_filter,
                )

            # Generate
            if chunks and self.ollama:
                ai_resp = await self.ollama.generate_answer(
                    question=clean_text,
                    chunks=chunks,
                    conversation_history=history,
                )
                if ai_resp:
                    response_text = ai_resp
                    source_files = list({c.source_file for c in chunks})
                    source_names = [SOURCE_FRIENDLY_NAMES.get(f, f) for f in source_files[:2]]
                    source_note = " & ".join(source_names)
                    used_ai = True
                else:
                    best = chunks[0]
                    response_text = best.text[:800]
                    source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)
                    ollama_failed = True
            elif chunks:
                best = chunks[0]
                response_text = best.text[:800]
                source_note = SOURCE_FRIENDLY_NAMES.get(best.source_file, best.source_file)
            else:
                response_text = (
                    "I wasn't able to find specific information about that "
                    "in the GSA knowledge base.\n\n"
                    "For accurate information, please:\n"
                    "- Visit the GSA office at *Campus Center 110A* (weekdays 11AM–5PM)\n"
                    "- Email us at *gsa-pres@njit.edu*\n"
                    "- Use /contact to find the right officer"
                )

            # Update conversation memory
            if self.conversation_manager:
                self.conversation_manager.add_turn(
                    user_id=user_id, role="user", content=clean_text
                )
                self.conversation_manager.add_turn(
                    user_id=user_id,
                    role="assistant",
                    content=response_text[:500],
                    source_files=[c.source_file for c in chunks],
                )

            # Log to DB
            if self.db:
                self.db.log_question(
                    user_id=int(user_id),
                    question=clean_text,
                    matched_topic=chunks[0].section_title if chunks else None,
                    confidence=chunks[0].relevance_score * 100 if chunks else 0.0,
                    guild_id=req.guild_id,
                )

            return MessageResponse(
                text=response_text,
                source_note=source_note,
                used_ai=used_ai,
                ollama_failed=ollama_failed,
            )

        except Exception as exc:
            logger.error("MessageHandler._rag_pipeline error: %s", exc, exc_info=True)
            return MessageResponse(
                text=(
                    "I encountered an error processing your question. "
                    "Please try again or contact a GSA officer at gsa-pres@njit.edu"
                )
            )
```

- [ ] **Step 2: Run all MessageHandler tests**

```bash
pytest bot/tests/test_message_handler.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 3: Run full test suite**

```bash
pytest bot/tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add bot/core/message_handler.py
git commit -m "feat: implement MessageHandler with intent routing and RAG pipeline"
```

---

### Task 5: Refactor `ChatCog` to delegate to `MessageHandler`

**Files:**
- Modify: `bot/commands/chat.py`
- Modify: `bot/main.py`

This replaces the inline RAG logic in `on_message` with a call to `MessageHandler.handle()`. The Discord-specific code (embed building, typing indicator, admin DM alert) stays in the cog.

- [ ] **Step 1: Update `GSABot.setup_hook()` in `bot/main.py`**

After the intent detector initialization block (around line 154), add before the `# ── Load all extensions` comment:

```python
        # ── Message handler ──────────────────────────────────────────────────
        from bot.core.message_handler import MessageHandler
        self.message_handler = MessageHandler(
            retriever=self.retriever,
            ollama=self.ollama,
            conversation_manager=self.conversation_manager,
            intent_detector=self.intent_detector,
            db=self.db,
            rate_limiter=self.rate_limiter,
            kb=self.kb,
            config=config,
        )
        logger.info("Message handler initialized")
```

Also add `self.message_handler = None` to `__init__` near the other `None` assignments (around line 64).

- [ ] **Step 2: Replace `bot/commands/chat.py` entirely**

```python
"""Free-form conversation handler — responds to natural language in #ask-gsa and DMs."""

import logging
import time

import discord
from discord.ext import commands

from bot.core.message_handler import MessageRequest

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_rgb(204, 0, 0)
_OLLAMA_ALERT_COOLDOWN = 3600


class ChatCog(commands.Cog, name="Chat"):
    """Handles free-form conversation in #ask-gsa channel and DMs."""

    _last_ollama_alert: float = 0.0

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = getattr(bot, "config", None)
        self.intent_detector = getattr(bot, "intent_detector", None)
        self.message_handler = getattr(bot, "message_handler", None)

    async def _notify_ollama_down(self, trigger_channel: discord.abc.Messageable) -> None:
        now = time.monotonic()
        if now - ChatCog._last_ollama_alert < _OLLAMA_ALERT_COOLDOWN:
            return
        ChatCog._last_ollama_alert = now
        admin_id = self.config.admin_discord_id if self.config else None
        if not admin_id:
            return
        try:
            user = await self.bot.fetch_user(admin_id)
            channel_ref = getattr(trigger_channel, "mention", str(trigger_channel))
            await user.send(
                f"⚠️ **GSA Gateway — LLM alert**\n"
                f"Ollama did not respond to a student question in {channel_ref}.\n"
                f"The bot fell back to raw KB text.\n\n"
                f"Check with: `systemctl status ollama` or `ollama ps`\n"
                f"Restart: `sudo systemctl restart ollama`\n\n"
                f"_(This alert won't repeat for 1 hour)_"
            )
        except Exception as exc:
            logger.warning("Could not DM admin about Ollama failure: %s", exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # GATE 1 — Ignore bots
        if message.author.bot:
            return

        # GATE 2 — Ignore slash commands
        if message.content.startswith("/"):
            return

        # GATE 3 — Determine if bot should respond
        channel_name = getattr(message.channel, "name", "DM")
        is_dm = isinstance(message.channel, discord.DMChannel)
        bot_user = self.bot.user
        bot_was_mentioned = (bot_user in message.mentions) if bot_user else False
        ask_gsa_channel = self.config.ask_gsa_channel if self.config else "ask-gsa"

        if self.intent_detector:
            should_respond = self.intent_detector.should_respond(
                message=message.content,
                channel_name=channel_name,
                bot_was_mentioned=bot_was_mentioned,
                ask_gsa_channel=ask_gsa_channel,
            )
        else:
            should_respond = channel_name == ask_gsa_channel or bot_was_mentioned

        if not should_respond and not is_dm:
            return

        # GATE 3.5 — Ignore member-to-member messages
        if not bot_was_mentioned and not is_dm:
            other_mentions = [u for u in message.mentions if u != bot_user]
            bot_mention_str = f"<@{bot_user.id}>" if bot_user else ""
            content_without_bot = message.content.replace(bot_mention_str, "").strip()
            if other_mentions or "<@" in content_without_bot:
                return

        # Clean text (strip bot mention)
        bot_mention = f"<@{bot_user.id}>" if bot_user else ""
        if self.intent_detector:
            clean_text = self.intent_detector.clean_message(
                message.content,
                bot_mention_string=bot_mention,
            )
        else:
            clean_text = message.content.replace(bot_mention, "").strip()

        if not clean_text:
            return

        # Delegate to MessageHandler
        async with message.channel.typing():
            try:
                req = MessageRequest(
                    user_id=str(message.author.id),
                    text=clean_text,
                    platform="discord",
                    guild_id=getattr(message.guild, "id", None),
                )
                resp = await self.message_handler.handle(req)

                if not resp.text:
                    return

                embed = discord.Embed(color=NJIT_RED)
                if len(resp.text) <= 4096:
                    embed.description = resp.text
                else:
                    embed.description = resp.text[:4093] + "..."

                footer_parts = ["💡 GSA Gateway"]
                if resp.source_note:
                    footer_parts.append(f"Source: {resp.source_note}")
                if resp.used_ai:
                    footer_parts.append("AI-generated from official GSA docs")
                embed.set_footer(text=" · ".join(footer_parts))

                await message.reply(embed=embed, mention_author=False)

                if resp.ollama_failed:
                    await self._notify_ollama_down(message.channel)

            except Exception as exc:
                logger.error("ChatCog on_message error: %s", exc, exc_info=True)
                try:
                    await message.reply(
                        "I encountered an error processing your question. "
                        "Please try again or contact a GSA officer at gsa-pres@njit.edu",
                        mention_author=False,
                    )
                except Exception:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
```

- [ ] **Step 3: Run existing tests**

```bash
pytest bot/tests/ -v
```

Expected: all tests pass. The `test_commands.py` tests that test `ChatCog` should still pass since they mock the bot object.

- [ ] **Step 4: Commit**

```bash
git add bot/commands/chat.py bot/main.py
git commit -m "refactor: delegate ChatCog RAG logic to MessageHandler"
```

---

### Task 6: Create `BasePlatform` ABC

**Files:**
- Create: `bot/connectors/base.py`

- [ ] **Step 1: Write the test**

Add `bot/tests/test_connectors.py`:

```python
import pytest
from bot.connectors.base import BasePlatform


def test_base_platform_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BasePlatform()


def test_subclass_without_all_methods_raises():
    class Incomplete(BasePlatform):
        async def start(self): pass
        # missing stop() and setup_services()

    with pytest.raises(TypeError):
        Incomplete()


def test_complete_subclass_can_be_instantiated():
    class Complete(BasePlatform):
        async def start(self): pass
        async def stop(self): pass
        async def setup_services(self): pass

    obj = Complete()
    assert obj is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest bot/tests/test_connectors.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `bot/connectors/base.py`**

```python
"""Abstract base class for all platform connectors."""

from abc import ABC, abstractmethod


class BasePlatform(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def setup_services(self) -> None: ...
```

- [ ] **Step 4: Run tests**

```bash
pytest bot/tests/test_connectors.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/connectors/base.py bot/tests/test_connectors.py
git commit -m "feat: add BasePlatform ABC for multi-platform connector pattern"
```

---

### Task 7: Add `telegram_token` to `Config`

**Files:**
- Modify: `bot/config.py`

- [ ] **Step 1: Add `telegram_token` field to `Config` dataclass**

In `bot/config.py`, add `telegram_token: str` after `admin_discord_id: int | None`:

```python
    # Telegram
    telegram_token: str
```

- [ ] **Step 2: Add to `load_config()`**

In `load_config()`, add `telegram_token=os.getenv("TELEGRAM_TOKEN", ""),` to the `Config(...)` call.

- [ ] **Step 3: Add to `.env.example` if it exists, else note it**

Check if `.env.example` exists:
```bash
ls /home/md724/gsa-gateway/.env.example 2>/dev/null && echo "exists" || echo "missing"
```

If it exists, add `TELEGRAM_TOKEN=` to it. If not, the engineer should note that `TELEGRAM_TOKEN` must be added to `.env` before running the Telegram bot.

- [ ] **Step 4: Run tests to ensure nothing broke**

```bash
pytest bot/tests/ -v
```

Expected: all tests pass (the new field has a default of `""` so existing tests aren't affected).

- [ ] **Step 5: Commit**

```bash
git add bot/config.py
git commit -m "feat: add telegram_token to Config"
```

---

### Task 8: Create `TelegramConnector`

**Files:**
- Create: `bot/connectors/telegram_connector.py`
- Create: `bot/tests/test_telegram_connector.py`

- [ ] **Step 1: Write failing tests**

Create `bot/tests/test_telegram_connector.py`:

```python
"""Tests for TelegramConnector command handlers."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageResponse
from bot.services.knowledge_base import Contact, Event, Resource


@pytest.fixture
def kb():
    kb = MagicMock()
    kb.events = [
        Event(
            name="GSA Mixer",
            date="2099-06-10",
            time="6:00 PM",
            location="Campus Center",
            description="Annual spring mixer.",
            organizer="GSA",
            rsvp_link="",
        )
    ]
    kb.contacts = {
        "president": Contact(
            role="GSA President",
            name="Fernando Vera",
            email="gsa-pres@njit.edu",
            office="Campus Center 110A",
        )
    }
    kb.resources = {
        "academic": [
            Resource(
                title="NJIT Library",
                description="Research databases.",
                url="https://library.njit.edu",
                category="academic",
            )
        ]
    }
    return kb


@pytest.fixture
def connector(kb):
    handler = MagicMock()
    handler.handle = AsyncMock(
        return_value=MessageResponse(text="Hello from GSA Gateway!")
    )
    return TelegramConnector(token="fake-token", handler=handler, kb=kb)


def _make_update_context(text="hello", args=None):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = 12345
    update.message = message
    context = MagicMock()
    context.args = args or []
    return update, context


@pytest.mark.asyncio
async def test_on_message_calls_handler_and_replies(connector):
    update, context = _make_update_context("what is gsa?")
    await connector._on_message(update, context)
    connector.handler.handle.assert_called_once()
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "hello from gsa gateway" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_appends_source_note(connector):
    connector.handler.handle = AsyncMock(
        return_value=MessageResponse(text="GSA info.", source_note="GSA FAQ")
    )
    update, context = _make_update_context("tell me about gsa")
    await connector._on_message(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa faq" in reply_text.lower()


@pytest.mark.asyncio
async def test_on_message_skips_empty_response(connector):
    connector.handler.handle = AsyncMock(return_value=MessageResponse(text=""))
    update, context = _make_update_context("  ")
    await connector._on_message(update, context)
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_events_lists_event_name(connector):
    update, context = _make_update_context("/events")
    await connector._cmd_events(update, context)
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa mixer" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_events_no_events(connector):
    connector.kb.events = []
    update, context = _make_update_context("/events")
    await connector._cmd_events(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "no upcoming events" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_no_args_lists_all(connector):
    update, context = _make_update_context("/contact")
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "fernando vera" in reply_text.lower()
    assert "gsa-pres@njit.edu" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_with_arg_filters(connector):
    update, context = _make_update_context("/contact president", args=["president"])
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "gsa president" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_contact_no_match(connector):
    update, context = _make_update_context("/contact zzz", args=["zzz"])
    await connector._cmd_contact(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "no matching" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_resources_no_args_lists_categories(connector):
    update, context = _make_update_context("/resources")
    await connector._cmd_resources(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "njit library" in reply_text.lower()


@pytest.mark.asyncio
async def test_cmd_resources_with_category(connector):
    update, context = _make_update_context("/resources academic", args=["academic"])
    await connector._cmd_resources(update, context)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "njit library" in reply_text.lower()
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest bot/tests/test_telegram_connector.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `bot/connectors/telegram_connector.py`**

```python
"""Telegram platform connector."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler
from telegram.ext import MessageHandler as PTBHandler
from telegram.ext import filters

from bot.connectors.base import BasePlatform
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class TelegramConnector(BasePlatform):
    def __init__(
        self, token: str, handler: MessageHandler, kb: KnowledgeBase
    ) -> None:
        self.token = token
        self.handler = handler
        self.kb = kb
        self.app: Optional[Application] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def setup_services(self) -> None:
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(PTBHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self.app.add_handler(CommandHandler("start", self._cmd_help))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("events", self._cmd_events))
        self.app.add_handler(CommandHandler("contact", self._cmd_contact))
        self.app.add_handler(CommandHandler("resources", self._cmd_resources))

    async def start(self) -> None:
        assert self.app is not None, "Call setup_services() before start()"
        self._stop_event = asyncio.Event()
        async with self.app:
            await self.app.start()
            await self.app.updater.start_polling()
            logger.info("Telegram bot polling — press Ctrl+C to stop")
            try:
                await self._stop_event.wait()
            finally:
                await self.app.updater.stop()
                await self.app.stop()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    async def _on_message(self, update: Update, context) -> None:
        if not update.message or not update.message.text:
            return
        req = MessageRequest(
            user_id=str(update.effective_user.id),
            text=update.message.text,
            platform="telegram",
        )
        resp = await self.handler.handle(req)
        if not resp.text:
            return
        text = resp.text
        if resp.source_note:
            text += f"\n\n_Source: {resp.source_note}_"
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)

    async def _cmd_events(self, update: Update, context) -> None:
        events = self.kb.events
        if not events:
            await update.message.reply_text("No upcoming events found.")
            return
        lines = ["*Upcoming GSA Events*\n"]
        for ev in events[:10]:
            lines.append(f"*{ev.name}*")
            lines.append(f"📅 {ev.date} at {ev.time}")
            lines.append(f"📍 {ev.location}")
            if ev.description:
                lines.append(str(ev.description)[:120])
            lines.append("")
        await update.message.reply_text(
            "\n".join(lines).strip(), parse_mode="Markdown"
        )

    async def _cmd_contact(self, update: Update, context) -> None:
        args = context.args or []
        contacts = list(self.kb.contacts.values())
        if args:
            query = " ".join(args).lower()
            contacts = [
                c for c in contacts
                if query in c.role.lower() or query in c.name.lower()
            ]
        if not contacts:
            await update.message.reply_text("No matching contacts found.")
            return
        lines = ["*GSA Contacts*\n"]
        for c in contacts[:10]:
            lines.append(f"*{c.name}* — {c.role}")
            lines.append(f"📧 {c.email}")
            if c.office and c.office != "N/A":
                lines.append(f"🏢 {c.office}")
            lines.append("")
        await update.message.reply_text(
            "\n".join(lines).strip(), parse_mode="Markdown"
        )

    async def _cmd_resources(self, update: Update, context) -> None:
        args = context.args or []
        resources = self.kb.resources
        if args:
            query = " ".join(args).lower()
            resources = {k: v for k, v in resources.items() if query in k.lower()}
        if not resources:
            available = ", ".join(self.kb.resources.keys())
            await update.message.reply_text(
                f"No resources found for that category.\n\nAvailable: {available}"
            )
            return
        lines = []
        for cat, items in list(resources.items())[:5]:
            lines.append(f"*{cat.title()}*")
            for item in items[:4]:
                line = f"• {item.title}"
                if item.url:
                    line += f": {item.url}"
                lines.append(line)
            lines.append("")
        await update.message.reply_text(
            "\n".join(lines).strip(), parse_mode="Markdown"
        )

    async def _cmd_help(self, update: Update, context) -> None:
        text = (
            "*GSA Gateway — Telegram Bot*\n\n"
            "I answer questions about NJIT's Graduate Student Association.\n\n"
            "*Commands:*\n"
            "/events — Upcoming GSA events\n"
            "/contact [role] — Find GSA officers\n"
            "/resources [category] — Campus resources\n"
            "/help — This message\n\n"
            "Or just type your question naturally!"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
```

- [ ] **Step 4: Run tests**

```bash
pytest bot/tests/test_telegram_connector.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest bot/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add bot/connectors/telegram_connector.py bot/tests/test_telegram_connector.py
git commit -m "feat: add TelegramConnector with Q&A and /events /contact /resources commands"
```

---

### Task 9: Create `run_telegram.py` entry point

**Files:**
- Create: `run_telegram.py`

- [ ] **Step 1: Create `run_telegram.py`**

Create at the repo root (same level as `bot/`):

```python
"""Telegram bot entry point — runs independently from the Discord bot."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.config import config
from bot.connectors.telegram_connector import TelegramConnector
from bot.core.message_handler import MessageHandler
from bot.services.conversation import ConversationManager
from bot.services.database import Database
from bot.services.embedder import EmbeddingService
from bot.services.intent_detector import IntentDetector
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter
from bot.services.retriever import Retriever
from bot.services.vector_store import VectorStore


def _configure_logging() -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=[logging.StreamHandler()])


_configure_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.telegram_token:
        logger.error("TELEGRAM_TOKEN is not set in .env — cannot start Telegram bot")
        sys.exit(1)

    # ── Services (mirrors bot/main.py setup_hook) ────────────────────────────
    db = Database(config.database_path)
    db.connect()
    db.init_tables()
    db.migrate_events_columns()
    db.migrate_rag_columns()

    kb = KnowledgeBase(data_dir=config.data_dir)
    kb.load()

    rate_limiter = RateLimiter(max_calls=5, period_seconds=60)
    conversation_manager = ConversationManager(
        timeout_minutes=config.conversation_timeout_minutes,
        max_turns=config.conversation_max_turns,
    )
    intent_detector = IntentDetector()

    embedder = EmbeddingService(base_url=config.ollama_url, model=config.embedding_model)
    embed_ok = await embedder.check_connection()
    if not embed_ok:
        logger.warning("Embedding model unavailable — semantic search disabled")
        embedder = None

    vector_store = VectorStore(db_path=config.chroma_db_path)
    retriever = None
    if embedder and not vector_store.is_empty():
        retriever = Retriever(embedder=embedder, vector_store=vector_store)
        logger.info("RAG retriever initialized: %d chunks", vector_store.get_chunk_count())
    else:
        logger.warning("Retriever not initialized — falling back to keyword search")

    ollama = None
    if config.ollama_enabled:
        from bot.services.ollama_client import OllamaClient
        ollama = OllamaClient(
            base_url=config.ollama_url,
            model=config.ollama_model,
            timeout=config.ollama_timeout,
            embedding_model=config.embedding_model,
        )
        await ollama.check_connection()
        logger.info("Ollama client initialized (model=%s)", config.ollama_model)

    handler = MessageHandler(
        retriever=retriever,
        ollama=ollama,
        conversation_manager=conversation_manager,
        intent_detector=intent_detector,
        db=db,
        rate_limiter=rate_limiter,
        kb=kb,
        config=config,
    )

    connector = TelegramConnector(
        token=config.telegram_token, handler=handler, kb=kb
    )
    await connector.setup_services()

    try:
        await connector.start()
    except asyncio.CancelledError:
        pass
    finally:
        await connector.stop()
        if embedder:
            await embedder.close()
        if ollama:
            await ollama.close()
        db.close()
        logger.info("Telegram bot shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: Verify the file is importable**

```bash
python -c "import ast; ast.parse(open('run_telegram.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest bot/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add run_telegram.py
git commit -m "feat: add run_telegram.py entry point for the Telegram connector"
```

---

### Task 10: Manual smoke test

Before running you need a Telegram bot token. Get one from [@BotFather](https://t.me/BotFather) on Telegram, then:

- [ ] **Step 1: Add token to `.env`**

```
TELEGRAM_TOKEN=<your-token-from-BotFather>
```

- [ ] **Step 2: Start the Telegram bot**

```bash
python run_telegram.py
```

Expected output:
```
[INFO] Telegram bot polling — press Ctrl+C to stop
```

- [ ] **Step 3: Open Telegram and test**

Send the bot a message: `hi`
Expected: greeting response mentioning GSA Gateway.

Send: `/events`
Expected: list of upcoming GSA events.

Send: `/contact`
Expected: list of GSA officers with emails.

Send: `/resources academic`
Expected: academic resources list.

Send: `how do I apply for a travel award?`
Expected: RAG response with source note.

- [ ] **Step 4: Verify Discord bot still works**

In a separate terminal:
```bash
python bot/main.py
```

Expected: Discord bot starts normally with no errors.

- [ ] **Step 5: Final commit (if any cleanup needed)**

```bash
git add -p  # stage only intentional changes
git commit -m "fix: smoke test cleanup"
```

---

## Adding a Third Platform (Reference)

When you need WhatsApp, Slack, etc.:

1. `pip install <library>` + add to `requirements.txt`
2. Create `bot/connectors/whatsapp_connector.py` — subclass `BasePlatform`, implement `setup_services()`, `start()`, `stop()`
3. In the message callback: `MessageRequest(user_id=..., text=..., platform="whatsapp")`
4. Copy `run_telegram.py` → `run_whatsapp.py`, swap `TelegramConnector` for `WhatsAppConnector`
5. Add `WHATSAPP_TOKEN` to `.env` and `Config`

No changes to `bot/services/`, `bot/commands/`, or existing connectors.

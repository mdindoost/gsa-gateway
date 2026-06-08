# Free Mode & Bot Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bot identity intent (answers "who are you?") and a free-mode toggle that bypasses the RAG/KB pipeline for general LLM conversation.

**Architecture:** Three files change. `IntentDetector` gains three new intent constants + pattern lists and two new detection branches. `ConversationSession` gains a `mode` field; `ConversationManager` gains `get_mode`/`set_mode`. `MessageHandler` gains three new intent handlers and a free-mode early-exit branch at the top of `_rag_pipeline`.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio (already installed), discord.py 2.x, Ollama

---

## File Map

| File | Change |
|------|--------|
| `bot/services/intent_detector.py` | Add `INTENT_IDENTITY`, `INTENT_FREE_MODE`, `INTENT_GSA_MODE` + three pattern lists + two detection branches in `detect()` |
| `bot/services/conversation.py` | Add `mode: str = "gsa"` to `ConversationSession`; add `get_mode()` and `set_mode()` to `ConversationManager` |
| `bot/core/message_handler.py` | Add `FREE_MODE_SYSTEM_PROMPT` constant; import 3 new intents; add 3 handlers in `handle()`; add free-mode routing branch at top of `_rag_pipeline()` |
| `bot/tests/test_intent_detector.py` | Append new test cases (identity, free mode, GSA mode) |
| `bot/tests/test_conversation.py` | Append mode field and get/set method tests |
| `bot/tests/test_message_handler.py` | Append identity + free mode handler + routing tests |

---

## Task 1: Intent Detector — New Intents and Patterns

**Files:**
- Modify: `bot/services/intent_detector.py`
- Test: `bot/tests/test_intent_detector.py`

- [ ] **Step 1: Append failing tests to `bot/tests/test_intent_detector.py`**

Add these at the end of the file. The imports at the top of the file need the three new constants added — add them to the existing import block:

```python
# Add to the import block at line 6 (alongside existing imports):
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FOOD,
    INTENT_FREE_MODE,   # new
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_IDENTITY,    # new
    INTENT_GSA_MODE,    # new
    INTENT_QUESTION,
    INTENT_STATEMENT,
    INTENT_THANKS,
    IntentDetector,
)
```

Append these test functions to the end of `bot/tests/test_intent_detector.py`:

```python
# ── Identity intent ───────────────────────────────────────────────────────────

def test_identity_who_are_you(detector):
    intent, conf = detector.detect("who are you")
    assert intent == INTENT_IDENTITY
    assert conf == 1.0


def test_identity_what_are_you(detector):
    intent, _ = detector.detect("what are you")
    assert intent == INTENT_IDENTITY


def test_identity_whats_your_name(detector):
    intent, _ = detector.detect("what's your name")
    assert intent == INTENT_IDENTITY


def test_identity_are_you_chatgpt(detector):
    intent, _ = detector.detect("are you chatgpt")
    assert intent == INTENT_IDENTITY


def test_identity_are_you_an_ai(detector):
    intent, _ = detector.detect("are you an ai")
    assert intent == INTENT_IDENTITY


def test_identity_what_model(detector):
    intent, _ = detector.detect("what model are you")
    assert intent == INTENT_IDENTITY


def test_identity_how_smart(detector):
    intent, _ = detector.detect("how smart are you")
    assert intent == INTENT_IDENTITY


def test_identity_does_not_shadow_help(detector):
    # "what can you do" must remain INTENT_HELP, not INTENT_IDENTITY
    intent, _ = detector.detect("what can you do")
    assert intent == INTENT_HELP


def test_regular_question_not_identity(detector):
    intent, _ = detector.detect("what is the travel award?")
    assert intent == INTENT_QUESTION


# ── Free mode intent ──────────────────────────────────────────────────────────

def test_free_mode_trigger(detector):
    intent, conf = detector.detect("free mode")
    assert intent == INTENT_FREE_MODE
    assert conf == 1.0


def test_free_mode_exclamation(detector):
    intent, _ = detector.detect("!free")
    assert intent == INTENT_FREE_MODE


def test_general_mode_trigger(detector):
    intent, _ = detector.detect("general mode")
    assert intent == INTENT_FREE_MODE


def test_switch_to_free_trigger(detector):
    intent, _ = detector.detect("switch to free")
    assert intent == INTENT_FREE_MODE


# ── GSA mode intent ───────────────────────────────────────────────────────────

def test_gsa_mode_trigger(detector):
    intent, conf = detector.detect("gsa mode")
    assert intent == INTENT_GSA_MODE
    assert conf == 1.0


def test_gsa_mode_exclamation(detector):
    intent, _ = detector.detect("!gsa")
    assert intent == INTENT_GSA_MODE


def test_switch_to_gsa_trigger(detector):
    intent, _ = detector.detect("switch to gsa")
    assert intent == INTENT_GSA_MODE


def test_free_mode_not_confused_with_clear(detector):
    # "free mode" must NOT match INTENT_CLEAR_HISTORY
    intent, _ = detector.detect("free mode")
    assert intent != INTENT_CLEAR_HISTORY
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/md724/gsa-gateway
python3 -m pytest bot/tests/test_intent_detector.py -k "identity or free_mode or gsa_mode" -v
```

Expected: `ImportError` or `FAILED` — the constants don't exist yet.

- [ ] **Step 3: Add constants to `bot/services/intent_detector.py`**

After line 14 (after `INTENT_HELP = "help"`), add:

```python
INTENT_IDENTITY = "identity"
INTENT_FREE_MODE = "free_mode"
INTENT_GSA_MODE = "gsa_mode"
```

- [ ] **Step 4: Add pattern lists to `bot/services/intent_detector.py`**

After the `SOCIAL_KEYWORDS` block (after line 97), add:

```python
IDENTITY_PATTERNS = [
    r"who are you",
    r"what are you",
    r"what'?s your name",
    r"\byour name\b",
    r"tell me about yourself",
    r"are you (an? )?(chatgpt|gpt|ai|bot|llm)",
    r"what model are you",
    r"which (llm|model)",
    r"what (llm|language model)",
    r"how smart are you",
]

FREE_MODE_PATTERNS = [
    r"^free mode$",
    r"^!free$",
    r"^general mode$",
    r"^switch to free",
    r"^freemode$",
]

GSA_MODE_PATTERNS = [
    r"^gsa mode$",
    r"^!gsa$",
    r"^switch to gsa",
    r"^gsamode$",
]
```

- [ ] **Step 5: Add detection branches inside `IntentDetector.detect()`**

**Branch 1** — After the clear-history check (after `return INTENT_CLEAR_HISTORY, 1.0`) and before the food check, add:

```python
        # 1b. Free mode / GSA mode toggle
        for pattern in FREE_MODE_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_FREE_MODE, 1.0
        for pattern in GSA_MODE_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_GSA_MODE, 1.0
```

**Branch 2** — After the help check (after `return INTENT_HELP, 1.0`) and before the question check (before `if msg.endswith("?")`), add:

```python
        # 7b. Identity (after HELP so "what can you do" stays as HELP)
        for pattern in IDENTITY_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_IDENTITY, 1.0
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
python3 -m pytest bot/tests/test_intent_detector.py -v
```

Expected: all green. If `test_identity_does_not_shadow_help` fails, verify branch 2 is inserted AFTER the HELP check, not before it.

- [ ] **Step 7: Commit**

```bash
git add bot/services/intent_detector.py bot/tests/test_intent_detector.py
git commit -m "feat: add INTENT_IDENTITY, INTENT_FREE_MODE, INTENT_GSA_MODE to intent detector"
```

---

## Task 2: ConversationSession Mode Field + Manager Methods

**Files:**
- Modify: `bot/services/conversation.py`
- Test: `bot/tests/test_conversation.py`

- [ ] **Step 1: Append failing tests to `bot/tests/test_conversation.py`**

```python
# ── Mode field ────────────────────────────────────────────────────────────────

def test_session_default_mode_is_gsa(manager):
    session = manager.get_or_create_session("user_mode_1")
    assert session.mode == "gsa"


def test_get_mode_returns_gsa_for_unknown_user(manager):
    assert manager.get_mode("never_seen") == "gsa"


def test_set_mode_to_free(manager):
    manager.set_mode("user_mode_2", "free")
    assert manager.get_mode("user_mode_2") == "free"


def test_set_mode_back_to_gsa(manager):
    manager.set_mode("user_mode_3", "free")
    manager.set_mode("user_mode_3", "gsa")
    assert manager.get_mode("user_mode_3") == "gsa"


def test_mode_resets_on_clear_session(manager):
    manager.set_mode("user_mode_4", "free")
    manager.clear_session("user_mode_4")
    # After clear, session is gone — get_mode must return default "gsa"
    assert manager.get_mode("user_mode_4") == "gsa"


def test_mode_is_per_user(manager):
    manager.set_mode("user_mode_5", "free")
    # A different user must still have the default
    assert manager.get_mode("user_mode_6") == "gsa"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest bot/tests/test_conversation.py -k "mode" -v
```

Expected: `FAILED` — `ConversationSession` has no `mode` attribute, `ConversationManager` has no `get_mode`/`set_mode`.

- [ ] **Step 3: Add `mode` field to `ConversationSession` in `bot/services/conversation.py`**

In the `ConversationSession` dataclass (around line 21), after the `message_count: int` field, add:

```python
    mode: str = "gsa"   # "gsa" | "free"
```

Full dataclass after the change:

```python
@dataclass
class ConversationSession:
    user_id: str
    turns: list[ConversationTurn]
    created_at: datetime
    last_active: datetime
    channel_id: Optional[str]
    message_count: int
    mode: str = "gsa"
```

- [ ] **Step 4: Add `get_mode` and `set_mode` to `ConversationManager`**

After the `clear_session` method (around line 143), add:

```python
    def get_mode(self, user_id: str) -> str:
        session = self.get_session(user_id)
        return session.mode if session is not None else "gsa"

    def set_mode(self, user_id: str, mode: str) -> None:
        session = self.get_or_create_session(user_id)
        session.mode = mode
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
python3 -m pytest bot/tests/test_conversation.py -v
```

Expected: all green, including the new mode tests.

- [ ] **Step 6: Commit**

```bash
git add bot/services/conversation.py bot/tests/test_conversation.py
git commit -m "feat: add mode field and get_mode/set_mode to ConversationManager"
```

---

## Task 3: MessageHandler — Identity Intent Handler

**Files:**
- Modify: `bot/core/message_handler.py`
- Test: `bot/tests/test_message_handler.py`

- [ ] **Step 1: Append failing tests to `bot/tests/test_message_handler.py`**

Add to the import block at the top (alongside existing intent imports):

```python
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FREE_MODE,    # new
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_IDENTITY,     # new
    INTENT_GSA_MODE,     # new
    INTENT_QUESTION,
    INTENT_THANKS,
)
```

Append these test functions:

```python
# ── Identity intent ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_identity_with_ollama_includes_model_name(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="who are you", platform="discord"))
    assert "GSA Gateway" in resp.text
    assert "llama3.1:8b" in resp.text
    assert resp.used_ai is False
    assert resp.source_note is None


@pytest.mark.asyncio
async def test_identity_without_ollama_omits_model_name(mock_services):
    mock_services["ollama"] = None
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="what are you", platform="telegram"))
    assert "GSA Gateway" in resp.text
    assert resp.text  # non-empty


@pytest.mark.asyncio
async def test_identity_does_not_call_retriever(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "mistral:7b"
    mock_services["intent_detector"].detect.return_value = (INTENT_IDENTITY, 1.0)
    h = MessageHandler(**mock_services)
    await h.handle(MessageRequest(user_id="u1", text="who are you", platform="discord"))
    mock_services["retriever"].retrieve.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest bot/tests/test_message_handler.py -k "identity" -v
```

Expected: `FAILED` — `INTENT_IDENTITY` is not yet imported in `message_handler.py`, no handler exists.

- [ ] **Step 3: Add `INTENT_IDENTITY` to imports in `bot/core/message_handler.py`**

Find the import block (lines 9–19). Add `INTENT_IDENTITY` to the existing `from bot.services.intent_detector import (...)` block:

```python
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FAREWELL,
    INTENT_FOOD,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_IDENTITY,
    INTENT_QUESTION,
    INTENT_SOCIAL,
    INTENT_THANKS,
)
```

- [ ] **Step 4: Add identity handler in `MessageHandler.handle()`**

In `handle()`, after the `INTENT_HELP` handler block (after the `return MessageResponse(text=...)` that lists commands), add:

```python
        if intent == INTENT_IDENTITY:
            model_name = self.ollama.model if self.ollama else None
            if model_name:
                text = (
                    "I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association.\n\n"
                    f"I'm powered by **{model_name}** — a local language model running on NJIT infrastructure, "
                    "not a cloud service. Unlike ChatGPT, I'm purpose-built for GSA: my answers come directly "
                    "from official GSA documents, policies, and contacts. I don't browse the internet or answer "
                    "general topics outside NJIT GSA.\n\n"
                    "Ask me about events, travel awards, club funding, officer contacts, or anything GSA-related!"
                )
            else:
                text = (
                    "I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association — "
                    "purpose-built to answer questions about GSA services, events, funding, and campus resources."
                )
            return MessageResponse(text=text)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
python3 -m pytest bot/tests/test_message_handler.py -k "identity" -v
```

Expected: 3 new tests pass. Run the full suite to confirm no regressions:

```bash
python3 -m pytest bot/tests/test_message_handler.py -v
```

- [ ] **Step 6: Commit**

```bash
git add bot/core/message_handler.py bot/tests/test_message_handler.py
git commit -m "feat: add identity intent handler to MessageHandler"
```

---

## Task 4: MessageHandler — Free Mode Toggle and Routing

**Files:**
- Modify: `bot/core/message_handler.py`
- Test: `bot/tests/test_message_handler.py`

- [ ] **Step 1: Append failing tests to `bot/tests/test_message_handler.py`**

Update the existing handler import line (add `FREE_MODE_SYSTEM_PROMPT`):

```python
from bot.core.message_handler import MessageHandler, MessageRequest, MessageResponse, FREE_MODE_SYSTEM_PROMPT
```

The intent imports from Task 3 Step 1 already include `INTENT_FREE_MODE` and `INTENT_GSA_MODE` — no change needed there.

Append these test functions:

```python
# ── Free mode toggle ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_free_mode_toggle_sets_mode_and_confirms(mock_services):
    mock_services["ollama"] = MagicMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["intent_detector"].detect.return_value = (INTENT_FREE_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="free mode", platform="discord"))
    assert "General Chat Mode" in resp.text
    mock_services["conversation_manager"].set_mode.assert_called_once_with("u1", "free")


@pytest.mark.asyncio
async def test_free_mode_unavailable_without_ollama(mock_services):
    mock_services["ollama"] = None
    mock_services["intent_detector"].detect.return_value = (INTENT_FREE_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="free mode", platform="discord"))
    assert "isn't available" in resp.text or "not available" in resp.text.lower()
    mock_services["conversation_manager"].set_mode.assert_not_called()


@pytest.mark.asyncio
async def test_gsa_mode_toggle_sets_mode_and_confirms(mock_services):
    mock_services["intent_detector"].detect.return_value = (INTENT_GSA_MODE, 1.0)
    h = MessageHandler(**mock_services)
    resp = await h.handle(MessageRequest(user_id="u1", text="gsa mode", platform="discord"))
    assert "GSA Mode" in resp.text
    mock_services["conversation_manager"].set_mode.assert_called_once_with("u1", "gsa")


# ── Free mode routing in _rag_pipeline ───────────────────────────────────────

@pytest.mark.asyncio
async def test_free_mode_skips_rag_and_calls_generate(mock_services):
    mock_services["ollama"] = AsyncMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["ollama"].generate = AsyncMock(return_value="Paris is the capital of France.")
    mock_services["conversation_manager"].get_mode.return_value = "free"
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    resp = await h.handle(
        MessageRequest(user_id="u1", text="what is the capital of France?", platform="discord")
    )
    assert resp.text == "Paris is the capital of France."
    assert resp.source_note == "General Chat Mode"
    mock_services["ollama"].generate.assert_called_once_with(
        prompt="what is the capital of France?",
        system=FREE_MODE_SYSTEM_PROMPT,
    )
    mock_services["retriever"].retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_free_mode_ollama_failure_returns_error_message(mock_services):
    mock_services["ollama"] = AsyncMock()
    mock_services["ollama"].model = "llama3.1:8b"
    mock_services["ollama"].generate = AsyncMock(return_value=None)
    mock_services["conversation_manager"].get_mode.return_value = "free"
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    resp = await h.handle(
        MessageRequest(user_id="u1", text="something", platform="discord")
    )
    assert resp.source_note == "General Chat Mode"
    assert "try again" in resp.text.lower()


@pytest.mark.asyncio
async def test_gsa_mode_still_uses_rag(mock_services):
    # Confirm that in "gsa" mode (default), retriever is still called
    mock_services["conversation_manager"].get_mode.return_value = "gsa"
    mock_services["retriever"].retrieve = AsyncMock(return_value=[])
    mock_services["intent_detector"].detect.return_value = (INTENT_QUESTION, 0.9)
    h = MessageHandler(**mock_services)
    await h.handle(MessageRequest(user_id="u1", text="what is the travel award?", platform="discord"))
    mock_services["retriever"].retrieve.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest bot/tests/test_message_handler.py -k "free_mode or gsa_mode" -v
```

Expected: `FAILED` or `ImportError` — `FREE_MODE_SYSTEM_PROMPT` doesn't exist, `INTENT_FREE_MODE`/`INTENT_GSA_MODE` not imported in handler.

- [ ] **Step 3: Add `INTENT_FREE_MODE` and `INTENT_GSA_MODE` to imports in `bot/core/message_handler.py`**

Update the intent import block:

```python
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FAREWELL,
    INTENT_FOOD,
    INTENT_FREE_MODE,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_IDENTITY,
    INTENT_GSA_MODE,
    INTENT_QUESTION,
    INTENT_SOCIAL,
    INTENT_THANKS,
)
```

- [ ] **Step 4: Add `FREE_MODE_SYSTEM_PROMPT` constant in `bot/core/message_handler.py`**

After the `_OFFICER_FIRST_NAMES` set (around line 26), add:

```python
FREE_MODE_SYSTEM_PROMPT = (
    "You are GSA Gateway, the official AI assistant for NJIT's Graduate Student "
    "Association. The student has switched to general chat mode. Answer helpfully "
    "and conversationally. You may answer questions beyond GSA topics, but "
    "periodically remind students you can also help with GSA events, funding, "
    "and campus resources."
)
```

- [ ] **Step 5: Add `INTENT_FREE_MODE` and `INTENT_GSA_MODE` handlers in `MessageHandler.handle()`**

After the `INTENT_IDENTITY` handler (after its `return MessageResponse(text=text)`), add:

```python
        if intent == INTENT_FREE_MODE:
            if not self.ollama:
                return MessageResponse(
                    text=(
                        "General chat mode requires the AI engine, which isn't available right now. "
                        "I'll continue answering GSA questions from the knowledge base."
                    )
                )
            if self.conversation_manager:
                self.conversation_manager.set_mode(user_id, "free")
            return MessageResponse(
                text="Switched to **General Chat Mode**. Ask me anything! Type `gsa mode` to return to GSA topics."
            )

        if intent == INTENT_GSA_MODE:
            if self.conversation_manager:
                self.conversation_manager.set_mode(user_id, "gsa")
            return MessageResponse(
                text="Switched back to **GSA Mode**. I'll answer from official GSA documents."
            )
```

- [ ] **Step 6: Add free-mode routing branch at the top of `_rag_pipeline()`**

Inside `_rag_pipeline()`, at the very start of the `try:` block (before `chunks = []`), add:

```python
            # Free mode: skip RAG entirely, go direct to LLM
            mode = self.conversation_manager.get_mode(user_id) if self.conversation_manager else "gsa"
            if mode == "free" and self.ollama:
                result = await self.ollama.generate(prompt=clean_text, system=FREE_MODE_SYSTEM_PROMPT)
                if self.conversation_manager:
                    self.conversation_manager.add_turn(user_id=user_id, role="user", content=clean_text)
                    if result:
                        self.conversation_manager.add_turn(
                            user_id=user_id, role="assistant", content=result[:500]
                        )
                return MessageResponse(
                    text=result or "The AI engine didn't respond. Please try again.",
                    source_note="General Chat Mode",
                )
```

- [ ] **Step 7: Run new tests to confirm they pass**

```bash
python3 -m pytest bot/tests/test_message_handler.py -k "free_mode or gsa_mode" -v
```

Expected: all 6 new tests pass.

- [ ] **Step 8: Run the full test suite to confirm no regressions**

```bash
python3 -m pytest bot/tests/ -v
```

Expected: all green. Pay attention to any existing `test_message_handler.py` tests — the only risky one is `test_question_no_chunks_returns_fallback`, which now also calls `get_mode` on the mocked conversation manager. Since `MagicMock` auto-creates `get_mode` returning a `MagicMock` (not `"free"`), the free-mode branch won't fire and the test will still pass. If it does fail, add `mock_services["conversation_manager"].get_mode.return_value = "gsa"` to the `mock_services` fixture in that test or in the shared fixture.

- [ ] **Step 9: Commit**

```bash
git add bot/core/message_handler.py bot/tests/test_message_handler.py
git commit -m "feat: add free mode toggle and direct LLM routing to MessageHandler"
```

---

## Verification

After all tasks complete, run the full suite one final time:

```bash
python3 -m pytest bot/tests/ -v --tb=short
```

Then do a quick manual smoke test (if the bot is running locally):
1. Type `who are you` → should see model name in response
2. Type `free mode` → should see "General Chat Mode" confirmation
3. Ask a general question (e.g., `what is the capital of France?`) → LLM answers directly, footer shows "General Chat Mode"
4. Type `gsa mode` → should see "GSA Mode" confirmation
5. Ask a GSA question → goes through RAG as normal

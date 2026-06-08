# Free Mode & Bot Identity — Design Spec

**Date:** 2026-06-08
**Author:** Mohammad Dindoost
**Status:** Approved

---

## Overview

Two features:

1. **Bot Identity** — the bot answers "who are you?" questions with its name, underlying model (read dynamically from config), and a plain-language comparison to ChatGPT.
2. **Free Mode** — users can toggle a per-session mode that bypasses the KB/RAG pipeline and sends messages directly to the LLM for general conversation.

Both features work identically on Discord and Telegram (platform-agnostic).

---

## Feature 1: Bot Identity (`INTENT_IDENTITY`)

### Intent patterns (added to `intent_detector.py`)

Inserted in the detection chain **after HELP (step 7) and before QUESTION (step 8)**, so existing HELP patterns are not shadowed. Identity questions still never reach RAG.

```
"who are you", "what are you", "what's your name", "your name",
"tell me about yourself", "are you chatgpt", "are you an ai",
"are you a bot", "what model are you", "which llm", "which model",
"what language model", "how smart are you"
```

### Response

Handled in `MessageHandler.handle()`. Model name is read from `self.ollama.model` at call time — no hardcoded string. If Ollama is not enabled, model name is omitted.

```
I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association.

I'm powered by **{model_name}** — a local language model running on NJIT infrastructure, not a cloud service. Unlike ChatGPT, I'm purpose-built for GSA: my answers come directly from official GSA documents, policies, and contacts. I don't browse the internet or answer general topics outside NJIT GSA.

Ask me about events, travel awards, club funding, officer contacts, or anything GSA-related!
```

When model name is unavailable (Ollama disabled):
```
I'm **GSA Gateway**, the official AI assistant for NJIT's Graduate Student Association — purpose-built to answer questions about GSA services, events, funding, and campus resources.
```

### No RAG, no DB log

Identity responses are hardcoded replies — no retrieval, no question logging.

---

## Feature 2: Free Mode (KB Bypass)

### Concept

Each `ConversationSession` gains a `mode` field (`"gsa"` | `"free"`). When mode is `"free"`, the RAG pipeline is skipped and messages go directly to `ollama.generate()` with a lightweight system prompt. Mode resets to `"gsa"` on session expiry (60 min inactivity) or when the user types `clear`.

### Trigger phrases

**Enter free mode** (`INTENT_FREE_MODE`):
- `free mode`, `!free`, `general mode`, `switch to free`, `freemode`

**Return to GSA mode** (`INTENT_GSA_MODE`):
- `gsa mode`, `!gsa`, `switch to gsa`, `gsamode`

`clear` (existing `INTENT_CLEAR_HISTORY`) also resets mode to `"gsa"`.

### Data flow

```
User: "free mode"
  → INTENT_FREE_MODE
  → ollama available? yes → set session.mode = "free"
  → Response: "Switched to General Chat Mode..."

User: "what is quantum computing?"
  → INTENT_QUESTION
  → session.mode == "free" → skip RAG
  → ollama.generate(prompt=question, system=FREE_MODE_SYSTEM_PROMPT)
  → Response: <LLM answer>  [footer: GSA Gateway · General Chat Mode]

User: "gsa mode" or "clear"
  → set session.mode = "gsa"
  → Response: "Switched back to GSA Mode."
```

### Graceful degradation

If the user types `free mode` but Ollama is not enabled:
```
General chat mode requires the AI engine, which isn't available right now.
I'll continue answering GSA questions from the knowledge base.
```
Session mode is not changed.

### Free mode system prompt (constant in `message_handler.py`)

```
You are GSA Gateway, the official AI assistant for NJIT's Graduate Student
Association. The student has switched to general chat mode. Answer helpfully
and conversationally. You may answer questions beyond GSA topics, but
periodically remind students you can also help with GSA events, funding,
and campus resources.
```

### Footer

- GSA mode (existing): `💡 GSA Gateway · Source: <doc name> · AI-generated from official GSA docs`
- Free mode: `💡 GSA Gateway · General Chat Mode`

`source_note="General Chat Mode"` is set on the `MessageResponse` returned from the free-mode branch. No changes to `ChatCog` or `TelegramConnector` required.

---

## Files Changed

| File | Change |
|------|--------|
| `bot/services/intent_detector.py` | Add `INTENT_IDENTITY`, `INTENT_FREE_MODE`, `INTENT_GSA_MODE` constants and pattern lists |
| `bot/services/conversation.py` | Add `mode: str = "gsa"` to `ConversationSession`; add `set_mode()` and `get_mode()` to `ConversationManager` |
| `bot/core/message_handler.py` | Add `FREE_MODE_SYSTEM_PROMPT` constant; handle 3 new intents; add free-mode routing branch at top of `_rag_pipeline`; reset mode in `INTENT_CLEAR_HISTORY` handler |

**No changes** to: `ChatCog`, `TelegramConnector`, `OllamaClient`, any slash command files, or any data files.

---

## What This Does Not Change

- RAG pipeline behavior in GSA mode is completely unchanged
- Conversation history is still tracked in free mode (multi-turn works)
- Rate limiting applies in free mode
- DB question logging is skipped in free mode (general questions are not GSA data)
- Admin commands are unaffected

---

## Open Questions / Future Work

- Consider adding a `/mode` slash command for Discord users who prefer the command palette over typing
- Free mode conversation history could optionally be kept separate from GSA mode history
- Model benchmark numbers (vs ChatGPT) could be added to identity response as a config value in a future iteration

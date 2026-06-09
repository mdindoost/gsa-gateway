# Unified Assistant — one shared brain for Discord + Telegram

**Status:** approved 2026-06-09 · **Author:** Mohammad + assistant

## Problem

Discord (`bot/main.py`) and Telegram (`run_telegram.py`) run as **two separate
processes**, and each one *independently* wires up its own retriever, Ollama
client, conversation manager, intent detector, and `MessageHandler`. There is no
shared construction. They share the `MessageHandler` *class* but not its wiring.

Consequence: the two brains **drift**. Concretely, when the v2 retriever was
added it was wired only into the Discord process; Telegram kept the old v1
ChromaDB retriever, so Telegram couldn't find content (e.g. the crawled CS
faculty) that Discord could. Every retriever/LLM change must be made in two
places or the platforms diverge.

Output is similarly split: the Telegram process replies via `bot.connectors`
(v1) while the v2 scheduler uses `v2.core.connectors` — two connector systems.

## Goals

1. **One place** wires the assistant brain; both entry points use it → drift becomes impossible.
2. **Preserve per-user isolation.** With N users across both platforms, each has
   their own conversation history and mode (free/gsa). This must not regress.
3. **Allow concurrency.** Multiple users querying at once must not serialize
   behind one another unnecessarily.
4. Keep **two processes** — Discord (`discord.py`) and Telegram
   (`python-telegram-bot`) each need their own event loop. We unify *wiring*, not processes.

## Non-goals

- A central brain *service* with IPC (overkill at this scale).
- Retiring v1 / ChromaDB (tracked separately in `POST_WORLDCUP_CLEANUP.md`).
- Converging the two connector systems for *replies* — replies are inherently
  per-platform; broadcasts already unify via the v2 registry. (Noted as future.)

## Design

### 1. A single builder: `bot/core/assistant.py`

```python
@dataclass
class Assistant:
    embedder; retriever; ollama; conversation_manager
    intent_detector; message_handler

def build_assistant(config, db, kb, rate_limiter) -> Assistant:
    # ONE definition of the brain, used by both entry points:
    #  - retriever: V2RetrieverShim if V2_RETRIEVER_ENABLED else v1 Retriever
    #  - ollama:    OllamaClient if OLLAMA_ENABLED
    #  - conversation_manager, intent_detector
    #  - MessageHandler wired with all of the above
    ...
```

`db`, `kb`, `rate_limiter` are passed in (each process loads those; other cogs
need direct references). Everything *drift-prone* (retriever/LLM/handler wiring)
lives in `build_assistant` and nowhere else.

### 2. Both entry points call it

- `bot/main.py` (`setup_hook`): replace the inline "Wire A" + Ollama +
  conversation + intent + `MessageHandler` block with
  `asst = build_assistant(config, self.db, self.kb, self.rate_limiter)` and
  assign `self.retriever = asst.retriever`, `self.message_handler = asst.message_handler`, etc.
- `run_telegram.py`: replace its inline construction with the same call.

Result: identical wiring, one source of truth.

### 3. Per-user isolation (unchanged, made explicit)

`ConversationManager.sessions` is keyed by `user_id`, so each user already has an
independent session (history + free/gsa mode). Discord uses `str(author.id)`,
Telegram its own id; the two processes have separate managers, so ids cannot
collide. The builder preserves this exactly. *(If the processes were ever merged,
sessions would key by `f"{platform}:{user_id}"`; documented for the future.)*

### 4. Concurrency

The v2 shim currently uses `Semaphore(max_concurrency=1)`, serializing all
retrieval within a process. Because each call opens its **own** SQLite connection,
concurrent reads are safe. Raise the default (→ 4) so simultaneous users don't
queue behind each other.

## Risks & rollback

- Both entry points change. If the builder misbehaves, the `V2_RETRIEVER_ENABLED`
  flag still selects v1 inside the builder, and `restart.sh` reverts.
- Mitigation: the builder keeps the existing v1 path as the flag's `else` branch.

## Implementation steps

1. Add `bot/core/assistant.py` (`Assistant` + `build_assistant`).
2. Refactor `bot/main.py` to use it.
3. Refactor `run_telegram.py` to use it.
4. Raise the shim's `max_concurrency` default to 4.
5. Verify: both processes start; both log the **same** retriever; the dean/faculty
   query works on **both**; per-user isolation intact (structural, by `user_id`).

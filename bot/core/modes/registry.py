"""Unified per-user mode model — the single source of truth for the bot's 5 modes.

Design: docs/superpowers/specs/2026-06-19-unify-modes-design.md

There is exactly ONE writer per fact:
  - the gsa/free *conversation* bit lives in ``ConversationModeStore`` (this module);
  - the judge/presenter/audience *judging* state lives in ``JudgingSessionManager`` and is
    *derived* (never mirrored) via its ``mode_of()`` projection.

``ModeRegistry.get()`` composes the two single-writer sources into the one effective mode,
so callers ask in exactly one place. This is "derive, don't mirror": no duplicate enum is
written, so the two views can never silently disagree.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Optional, Union


class Mode(str, Enum):
    """The single vocabulary of user modes.

    ``str``-valued so existing persisted/logged values keep working unchanged:
    ``Mode.FREE == "free"`` is True and ``Mode.FREE.value == "free"``.
    """

    GSA = "gsa"            # default — GSA knowledge (structured + RAG)
    FREE = "free"          # general chat — skip GSA knowledge, general LLM
    JUDGE = "judge"        # judging state machine (Telegram only)
    PRESENTER = "presenter"
    AUDIENCE = "audience"

    @property
    def is_judging(self) -> bool:
        return self in (Mode.JUDGE, Mode.PRESENTER, Mode.AUDIENCE)


# Accepts a Mode or a legacy bare string at the boundary.
ModeLike = Union[Mode, str]


class ConversationModeStore:
    """Per-process, in-memory store of the conversation mode (GSA|FREE) ONLY.

    Keyed by raw ``user_id`` — each platform is its own process, so a Discord id and a
    Telegram id never collide (see ``bot/core/assistant.py``). Default is GSA.

    Thread-safety: a single lock wraps the dict op (nanoseconds). ``message_handler``'s
    ``_try_structured`` reads the mode from an ``asyncio.to_thread`` worker thread while the
    event loop may write it (free/gsa toggle); the lock makes each op atomic. It never calls
    back into async, so there is no deadlock. The lock's scope is this store only — it makes
    no claim about the judging session machine's internal state.
    """

    def __init__(self) -> None:
        self._modes: dict[str, Mode] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> Mode:
        with self._lock:
            return self._modes.get(user_id, Mode.GSA)

    def set(self, user_id: str, mode: ModeLike) -> None:
        m = Mode(mode)  # normalize legacy strings ("free"/"gsa") to the enum
        with self._lock:
            self._modes[user_id] = m

    def reset(self, user_id: str) -> None:
        """Return the user to the default (GSA) — equivalent to removing the entry."""
        with self._lock:
            self._modes.pop(user_id, None)


class ModeRegistry:
    """The ONE place to ask 'what mode is this user in', composing the two single-writer
    sources: judging-derived first (if a judging manager is wired), else conversation.

    ``judging`` (optional) is any object exposing ``mode_of(user_id) -> Mode | None``
    (typically a ``JudgingSessionManager``). Discord wires ``judging=None``.
    """

    def __init__(self, conv_store: ConversationModeStore, judging=None) -> None:
        self.conv_store = conv_store
        self.judging = judging

    def get(self, user_id: str) -> Mode:
        if self.judging is not None:
            jm: Optional[Mode] = self.judging.mode_of(user_id)
            if jm is not None:
                return jm
        return self.conv_store.get(user_id)

"""ModeDispatcher — the single connector entry point with EXPLICIT mode ownership.

Replaces the old implicit "judging intercepts before the conversation handler" ordering in
``telegram_connector._on_message`` with a stated invariant:

    Judging owns the message  iff  the user is already in a judging mode
                                    OR (the user is in a conversation mode {GSA, FREE}
                                        AND the text is a judging entry trigger).

Otherwise the conversation handler (the RAG/message_handler path, which owns the gsa/free
toggle and the free-mode-skips-structured gate) handles it.

Design: docs/superpowers/specs/2026-06-19-unify-modes-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from bot.core.modes.registry import Mode, ModeRegistry


@dataclass
class Reply:
    """Unified dispatcher result. ``is_judging`` tells the connector whether ``payload`` is
    a judging text reply (render as Markdown, no feedback buttons) or the conversation
    handler's native return value (e.g. a ``MessageResponse``)."""

    is_judging: bool
    payload: Any

    @property
    def text(self) -> Any:
        """Convenience: the judging text, or the conversation payload as-is."""
        return self.payload

    @classmethod
    def judging(cls, text: Optional[str]) -> "Reply":
        return cls(is_judging=True, payload=text)

    @classmethod
    def conversation(cls, payload: Any) -> "Reply":
        return cls(is_judging=False, payload=payload)


class ModeDispatcher:
    def __init__(
        self,
        registry: ModeRegistry,
        *,
        judging=None,
        conversation_handler: Callable[[Any], Awaitable[Any]],
    ) -> None:
        self.registry = registry
        self.judging = judging
        self.conversation_handler = conversation_handler

    async def dispatch(
        self,
        user_id: str,
        text: str,
        *,
        make_request: Callable[[str, str], Any],
    ) -> Reply:
        """Route one incoming message. ``make_request(user_id, text)`` builds the object the
        conversation handler expects (e.g. a ``MessageRequest``)."""
        mode = self.registry.get(user_id)  # unified, derived (judging ?? conversation)

        judging_owns = self.judging is not None and (
            mode.is_judging
            or (mode in (Mode.GSA, Mode.FREE) and self.judging.is_trigger(text))
        )

        if judging_owns:
            resp_text, consumed = self.judging.handle(user_id, text)
            if consumed:
                return Reply.judging(resp_text)
            # Only reachable from idle + non-trigger, which judging_owns already excludes;
            # defensive fall-through so a message is never dropped.

        payload = await self.conversation_handler(make_request(user_id, text))
        return Reply.conversation(payload)

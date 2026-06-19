"""Per-user conversation history manager for multi-turn dialogue."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    role: str
    content: str
    timestamp: datetime
    source_files: list[str] = field(default_factory=list)


@dataclass
class ConversationSession:
    user_id: str
    turns: list[ConversationTurn]
    created_at: datetime
    last_active: datetime
    channel_id: Optional[str]
    message_count: int
    mode: str = "gsa"


class ConversationManager:
    def __init__(
        self,
        timeout_minutes: int = 60,
        max_turns: int = 5,
        mode_store=None,
    ) -> None:
        self.sessions: dict[str, ConversationSession] = {}
        self.timeout_minutes = timeout_minutes
        self.max_turns = max_turns
        # Unified mode: the gsa/free bit is owned by a ConversationModeStore — the single
        # source of truth shared with the dispatcher/registry. get_mode/set_mode delegate to
        # it. When none is injected (e.g. in unit tests) we create a private one so the API
        # still works in isolation.
        if mode_store is None:
            from bot.core.modes import ConversationModeStore
            mode_store = ConversationModeStore()
        self.mode_store = mode_store
        self._cleanup_task: Optional[asyncio.Task] = None
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_loop())
        except RuntimeError:
            pass  # No running loop at init time; task created on first use

    def _ensure_cleanup_running(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._cleanup_loop())
            except RuntimeError:
                pass

    def _is_expired(self, session: ConversationSession) -> bool:
        now = datetime.now(timezone.utc)
        delta = now - session.last_active
        return delta.total_seconds() > (self.timeout_minutes * 60)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            expired = [
                uid for uid, session in list(self.sessions.items())
                if self._is_expired(session)
            ]
            for uid in expired:
                del self.sessions[uid]
                logger.debug("Session expired for user %s...", uid[:8])

    def get_session(self, user_id: str) -> Optional[ConversationSession]:
        session = self.sessions.get(user_id)
        if session is None:
            return None
        if self._is_expired(session):
            del self.sessions[user_id]
            return None
        return session

    def get_or_create_session(
        self,
        user_id: str,
        channel_id: Optional[str] = None,
    ) -> ConversationSession:
        self._ensure_cleanup_running()
        session = self.get_session(user_id)
        if session is not None:
            session.last_active = datetime.now(timezone.utc)
            return session

        now = datetime.now(timezone.utc)
        session = ConversationSession(
            user_id=user_id,
            turns=[],
            created_at=now,
            last_active=now,
            channel_id=channel_id,
            message_count=0,
        )
        self.sessions[user_id] = session
        return session

    def add_turn(
        self,
        user_id: str,
        role: str,
        content: str,
        source_files: Optional[list[str]] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        session = self.get_or_create_session(user_id, channel_id=channel_id)
        turn = ConversationTurn(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc),
            source_files=source_files or [],
        )
        session.turns.append(turn)
        session.message_count += 1
        session.last_active = datetime.now(timezone.utc)

        # Enforce max_turns (each turn = 1 user + 1 assistant message)
        while len(session.turns) > self.max_turns * 2:
            session.turns.pop(0)

        logger.debug(
            "Added %s turn for user %s..., session now has %d turns",
            role, user_id[:8], len(session.turns),
        )

    def get_history(
        self,
        user_id: str,
        max_turns: Optional[int] = None,
    ) -> list[dict]:
        session = self.get_session(user_id)
        if session is None:
            return []
        turns = session.turns
        if max_turns is not None:
            turns = turns[-(max_turns * 2):]
        return [{"role": t.role, "content": t.content} for t in turns]

    def clear_session(self, user_id: str) -> None:
        if user_id in self.sessions:
            del self.sessions[user_id]
        # Preserve the legacy contract: clearing the conversation also resets the mode to
        # the default (GSA). Mode now lives in the shared store, so reset it explicitly.
        self.mode_store.reset(user_id)
        logger.info("Session cleared for user %s...", user_id[:8])

    def get_mode(self, user_id: str) -> str:
        # Delegates to the shared ConversationModeStore (single source of truth). Returns the
        # plain string value ("gsa"/"free") for back-compat with callers that compare to a
        # bare string and with log_question(mode=...).
        return self.mode_store.get(user_id).value

    def set_mode(self, user_id: str, mode: str) -> None:
        self.mode_store.set(user_id, mode)

    def get_stats(self) -> dict:
        return {
            "active_sessions": len(self.sessions),
            "total_turns": sum(len(s.turns) for s in self.sessions.values()),
        }

    def format_history_for_prompt(self, user_id: str) -> str:
        history = self.get_history(user_id)
        if not history:
            return ""
        lines = ["Previous conversation:"]
        for turn in history:
            prefix = "Student" if turn["role"] == "user" else "GSA Gateway"
            lines.append(f"{prefix}: {turn['content'][:300]}")
        return "\n".join(lines)

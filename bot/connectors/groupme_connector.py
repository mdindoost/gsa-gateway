"""GroupMe platform connector.

GroupMe's bot model is asymmetric:

  * Outbound (the bot posting replies) only needs the **Bot ID** — a simple
    ``POST https://api.groupme.com/v3/bots/post`` with ``{bot_id, text}``. No token.
  * Inbound (receiving group messages) has two transports:
      - **Polling** (used here): periodically ``GET /v3/groups/{group_id}/messages``
        with an access token and react to anything new. Outbound-only network, so it
        works on the current local/SSH-tunnel deployment exactly like Telegram's
        long-poll.
      - **Webhook**: GroupMe POSTs each message to a public callback URL. Real-time,
        no token, but needs a public endpoint (the future NJIT server).

This connector implements polling now but keeps the message-processing logic in one
transport-agnostic place (:meth:`_process_message`). A webhook server only has to parse
the callback JSON and call :meth:`handle_callback`, which reuses the same path — see the
stub at the bottom. Like the Telegram connector, it runs as its own process
(``run_groupme.py``) and shares the assistant brain via ``build_assistant()``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp

from bot.connectors.base import BasePlatform
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

POST_URL = "https://api.groupme.com/v3/bots/post"
MESSAGES_URL = "https://api.groupme.com/v3/groups/{group_id}/messages"

# GroupMe rejects messages longer than 1000 characters; we chunk on safe boundaries.
MAX_GROUPME_LEN = 1000
# Max messages GroupMe returns per request (its hard cap is 100).
_FETCH_LIMIT = 100


def _strip_markdown(text: str) -> str:
    """GroupMe renders plain text only. Drop **bold**/__bold__ markers so students
    don't see literal asterisks. Bullets ('- ') and URLs are left untouched."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    return text


def _chunk(text: str, limit: int = MAX_GROUPME_LEN) -> list[str]:
    """Split text into <=limit pieces, preferring paragraph then line then hard cuts."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


class GroupMeConnector(BasePlatform):
    def __init__(
        self,
        *,
        bot_id: str,
        access_token: str,
        group_id: str,
        handler: MessageHandler,
        kb: KnowledgeBase,
        poll_interval: int = 5,
    ) -> None:
        self.bot_id = bot_id
        self.access_token = access_token
        self.group_id = group_id
        self.handler = handler
        self.kb = kb
        self.poll_interval = max(1, int(poll_interval))
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop_event: Optional[asyncio.Event] = None
        # id of the most recent message we've already seen, so we never replay history
        # or reprocess. Seeded to the latest message on start().
        self._last_id: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_services(self) -> None:
        if not self.bot_id:
            raise ValueError("GROUPME_BOT_ID is required to post replies")
        self._session = aiohttp.ClientSession()

    async def start(self) -> None:
        assert self._session is not None, "Call setup_services() before start()"
        self._stop_event = asyncio.Event()
        if not self.access_token or not self.group_id:
            logger.warning(
                "GroupMe inbound polling disabled (missing GROUPME_ACCESS_TOKEN or "
                "GROUPME_GROUP_ID). The bot can still post but won't read messages.")
            await self._stop_event.wait()
            return

        # Seed the cursor to the current latest message so we skip the backlog.
        self._last_id = await self._latest_message_id()
        logger.info("GroupMe polling started (group=%s, every %ss, cursor=%s)",
                    self.group_id, self.poll_interval, self._last_id)
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let one bad poll kill the loop
                logger.exception("GroupMe poll error: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Inbound: polling ──────────────────────────────────────────────────────

    async def _poll_once(self) -> None:
        if self._last_id is None:
            # Group had no messages at startup; establish a baseline without replying.
            self._last_id = await self._latest_message_id()
            return
        messages = await self._fetch_messages(after_id=self._last_id)
        # Process oldest -> newest and advance the cursor past every message we saw
        # (including bot/system ones) so they're never reconsidered.
        for msg in sorted(messages, key=lambda m: m.get("created_at", 0)):
            self._last_id = msg.get("id", self._last_id)
            if self._should_process(msg):
                await self._process_message(
                    user_id=msg.get("user_id") or msg.get("sender_id") or "unknown",
                    text=msg.get("text") or "",
                    name=msg.get("name"),
                )

    @staticmethod
    def _should_process(msg: dict) -> bool:
        # Only real users — skip our own posts (sender_type == "bot", which prevents
        # reply loops) and system join/leave events.
        return msg.get("sender_type") == "user" and bool((msg.get("text") or "").strip())

    async def _latest_message_id(self) -> Optional[str]:
        messages = await self._fetch_messages(limit=1)
        if not messages:
            return None
        newest = max(messages, key=lambda m: m.get("created_at", 0))
        return newest.get("id")

    async def _fetch_messages(
        self, *, after_id: Optional[str] = None, limit: int = _FETCH_LIMIT
    ) -> list[dict]:
        assert self._session is not None
        params: dict[str, str] = {"limit": str(limit)}
        if after_id:
            params["after_id"] = after_id
        url = MESSAGES_URL.format(group_id=self.group_id)
        headers = {"X-Access-Token": self.access_token}
        async with self._session.get(url, params=params, headers=headers) as resp:
            if resp.status == 304:  # GroupMe returns 304 when there are no newer messages
                return []
            if resp.status >= 300:
                logger.warning("GroupMe fetch failed (%s): %s", resp.status, await resp.text())
                return []
            data = await resp.json()
        return (data.get("response") or {}).get("messages") or []

    # ── Shared processing seam (poll today, webhook tomorrow) ──────────────────

    async def _process_message(self, *, user_id: str, text: str, name: Optional[str] = None) -> None:
        req = MessageRequest(user_id=str(user_id), text=text, platform="groupme")
        resp = await self.handler.handle(req)
        if not resp or not resp.text:
            return
        out = _strip_markdown(resp.text)
        if resp.source_note:
            out += f"\n\nSource: {resp.source_note}"
        await self._post(out)

    async def handle_callback(self, payload: dict) -> None:
        """Webhook entry point (not wired yet). A future public HTTP server parses the
        GroupMe callback JSON and calls this; it reuses the same processing path as
        polling. Kept here so adding webhook support is purely additive."""
        if not self._should_process(payload):
            return
        await self._process_message(
            user_id=payload.get("user_id") or payload.get("sender_id") or "unknown",
            text=payload.get("text") or "",
            name=payload.get("name"),
        )

    # ── Outbound: posting ──────────────────────────────────────────────────────

    async def _post(self, text: str) -> None:
        assert self._session is not None
        for chunk in _chunk(text):
            try:
                async with self._session.post(
                    POST_URL, json={"bot_id": self.bot_id, "text": chunk}
                ) as resp:
                    if resp.status >= 300:
                        logger.warning(
                            "GroupMe post failed (%s): %s", resp.status, await resp.text())
            except Exception as exc:  # noqa: BLE001
                logger.exception("GroupMe post error: %s", exc)

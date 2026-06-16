"""GroupMePostClient — transport client for the v2 GroupMe publishing connector.

Posts to a GroupMe group via ``POST /v3/bots/post`` using only the bot ID (no access
token). The ``channel`` argument is informational — a GroupMe bot is bound to one group
at creation time.
"""

from __future__ import annotations

import logging
import re

import aiohttp

logger = logging.getLogger(__name__)

POST_URL = "https://api.groupme.com/v3/bots/post"
MAX_LEN = 1000

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def _strip_markdown(text: str) -> str:
    return _BOLD.sub(lambda m: m.group(1) or m.group(2), text)


def _chunk(text: str, limit: int = MAX_LEN) -> list[str]:
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


class GroupMePostClient:
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self._session: aiohttp.ClientSession | None = None

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_message(self, channel, content, **kw) -> str:
        session = await self._session_or_create()
        plain = _strip_markdown(content)
        last_status = 0
        for piece in _chunk(plain):
            async with session.post(
                POST_URL, json={"bot_id": self.bot_id, "text": piece}
            ) as resp:
                last_status = resp.status
                if resp.status >= 300:
                    body = await resp.text()
                    raise RuntimeError(f"GroupMe post failed ({resp.status}): {body}")
        return f"groupme:{self.bot_id}:{last_status}"

    async def ping(self) -> bool:
        return bool(self.bot_id)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

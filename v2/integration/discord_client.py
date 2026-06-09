"""DiscordClientAdapter — transport client for the v2 DiscordConnector.

Wraps the running discord.py bot. Resolves a channel *name* (as stored in
settings, e.g. "gsa-announcements") to a channel and sends. This is the
``client`` the v2 ``DiscordConnector`` calls (``send_message``/``ping``).
"""

from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)


class DiscordClientAdapter:
    def __init__(self, bot):
        self.bot = bot

    def _resolve(self, channel_name):
        if not channel_name:
            return None
        for guild in self.bot.guilds:
            ch = discord.utils.get(guild.text_channels, name=channel_name)
            if ch:
                return ch
        return None

    async def send_message(self, channel, content, media_path=None, buttons=None):
        ch = self._resolve(channel)
        if ch is None:
            raise RuntimeError(f"Discord channel '{channel}' not found")
        if media_path:
            msg = await ch.send(content=content, file=discord.File(media_path))
        else:
            msg = await ch.send(content=content)
        return msg.id

    async def ping(self) -> bool:
        return self.bot.is_ready()

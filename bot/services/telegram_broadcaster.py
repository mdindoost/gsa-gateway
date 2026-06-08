"""Lightweight Telegram channel broadcaster — send-only, no polling."""

import logging
from pathlib import Path
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from bot.config import config

logger = logging.getLogger(__name__)


class TelegramBroadcaster:
    """Send messages and photos to a Telegram channel without polling."""

    def __init__(self, token: str) -> None:
        self._bot = Bot(token=token)

    async def broadcast(
        self,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        target = config.telegram_broadcast_target
        if not target:
            logger.debug("No Telegram broadcast target configured")
            return False
        try:
            await self._bot.send_message(
                chat_id=target,
                text=text,
                parse_mode=parse_mode,
            )
            logger.info("Broadcast sent to Telegram channel")
            return True
        except TelegramError as exc:
            logger.warning("Telegram broadcast failed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Telegram broadcast error: %s", exc)
            return False

    async def broadcast_photo(
        self,
        photo_path: str,
        caption: str,
        parse_mode: str = "HTML",
    ) -> bool:
        target = config.telegram_broadcast_target
        if not target:
            return False
        path = Path(photo_path)
        if not path.exists():
            logger.debug("Photo not found at %s — falling back to text", photo_path)
            return await self.broadcast(caption, parse_mode=parse_mode)
        try:
            with open(path, "rb") as photo:
                await self._bot.send_photo(
                    chat_id=target,
                    photo=photo,
                    caption=caption[:1024],
                    parse_mode=parse_mode,
                )
            logger.info("Photo broadcast sent to Telegram")
            return True
        except TelegramError as exc:
            logger.warning("Telegram photo broadcast failed: %s — falling back to text", exc)
            return await self.broadcast(caption, parse_mode=parse_mode)
        except Exception as exc:
            logger.warning("Telegram photo broadcast error: %s — falling back to text", exc)
            return await self.broadcast(caption, parse_mode=parse_mode)

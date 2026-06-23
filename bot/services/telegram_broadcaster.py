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
    ):
        """Send a text message. Returns the sent ``telegram.Message`` (carrying the real
        ``message_id`` and ``chat.id``) on success, or ``None`` on no-target/failure. The
        Message — not a bool — is returned so callers can persist the real id for later
        unsend/audit (scheduled-deletion Phase 0)."""
        target = config.telegram_broadcast_target
        if not target:
            logger.debug("No Telegram broadcast target configured")
            return None
        try:
            msg = await self._bot.send_message(
                chat_id=target,
                text=text,
                parse_mode=parse_mode,
            )
            logger.info("Broadcast sent to Telegram channel")
            return msg
        except TelegramError as exc:
            logger.warning("Telegram broadcast failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Telegram broadcast error: %s", exc)
            return None

    async def delete(self, chat_id, message_id) -> bool:
        """Unsend a previously-sent message. Returns True on success and lets the
        ``telegram.error`` PROPAGATE on failure, so the connector can classify it
        (terminal: no-rights/expiry vs transient: rate-limit/network). message_id is
        coerced to int (Telegram's API types it as int)."""
        await self._bot.delete_message(chat_id=chat_id, message_id=int(message_id))
        return True

    async def broadcast_photo(
        self,
        photo_path: str,
        caption: str,
        parse_mode: str = "HTML",
    ):
        """Send a photo with caption. Returns the sent ``telegram.Message`` on success, or
        ``None`` on failure; falls back to a text ``broadcast`` (also returning its Message|None)
        when the file is missing or the photo send errors."""
        target = config.telegram_broadcast_target
        if not target:
            return None
        path = Path(photo_path)
        if not path.exists():
            logger.debug("Photo not found at %s — falling back to text", photo_path)
            return await self.broadcast(caption, parse_mode=parse_mode)
        try:
            with open(path, "rb") as photo:
                msg = await self._bot.send_photo(
                    chat_id=target,
                    photo=photo,
                    caption=caption[:1024],
                    parse_mode=parse_mode,
                )
            logger.info("Photo broadcast sent to Telegram")
            return msg
        except TelegramError as exc:
            logger.warning("Telegram photo broadcast failed: %s — falling back to text", exc)
            return await self.broadcast(caption, parse_mode=parse_mode)
        except Exception as exc:
            logger.warning("Telegram photo broadcast error: %s — falling back to text", exc)
            return await self.broadcast(caption, parse_mode=parse_mode)

"""Telegram platform connector."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler
from telegram.ext import MessageHandler as PTBHandler
from telegram.ext import filters

from bot.connectors.base import BasePlatform
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class TelegramConnector(BasePlatform):
    def __init__(
        self, token: str, handler: MessageHandler, kb: KnowledgeBase
    ) -> None:
        self.token = token
        self.handler = handler
        self.kb = kb
        self.app: Optional[Application] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def setup_services(self) -> None:
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(PTBHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self.app.add_handler(CommandHandler("start", self._cmd_help))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("events", self._cmd_events))
        self.app.add_handler(CommandHandler("contact", self._cmd_contact))
        self.app.add_handler(CommandHandler("resources", self._cmd_resources))

    async def start(self) -> None:
        assert self.app is not None, "Call setup_services() before start()"
        self._stop_event = asyncio.Event()
        async with self.app:
            await self.app.start()
            await self.app.updater.start_polling()
            logger.info("Telegram bot polling — press Ctrl+C to stop")
            try:
                await self._stop_event.wait()
            finally:
                await self.app.updater.stop()
                await self.app.stop()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    async def _on_message(self, update: Update, context) -> None:
        if not update.message or not update.message.text or not update.effective_user:
            return
        req = MessageRequest(
            user_id=str(update.effective_user.id),
            text=update.message.text,
            platform="telegram",
        )
        resp = await self.handler.handle(req)
        if not resp.text:
            return
        text = resp.text
        if resp.source_note:
            text += f"\n\n_Source: {resp.source_note}_"
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)

    async def _cmd_events(self, update: Update, context) -> None:
        if not update.message:
            return
        events = self.kb.events
        if not events:
            await update.message.reply_text("No upcoming events found.")
            return
        lines = ["*Upcoming GSA Events*\n"]
        for ev in events[:10]:
            lines.append(f"*{ev.name}*")
            lines.append(f"📅 {ev.date} at {ev.time}")
            lines.append(f"📍 {ev.location}")
            if ev.description:
                lines.append(str(ev.description)[:120])
            lines.append("")
        text = "\n".join(lines).strip()
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Markdown parse failed in _cmd_events, sending plain: %s", exc)
            await update.message.reply_text(text)

    async def _cmd_contact(self, update: Update, context) -> None:
        if not update.message:
            return
        args = context.args or []
        contacts = list(self.kb.contacts.values())
        if args:
            query = " ".join(args).lower()
            contacts = [
                c for c in contacts
                if query in c.role.lower() or query in c.name.lower()
            ]
        if not contacts:
            await update.message.reply_text("No matching contacts found.")
            return
        lines = ["*GSA Contacts*\n"]
        for c in contacts[:10]:
            lines.append(f"*{c.name}* — {c.role}")
            lines.append(f"📧 {c.email}")
            if c.office and c.office != "N/A":
                lines.append(f"🏢 {c.office}")
            lines.append("")
        text = "\n".join(lines).strip()
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Markdown parse failed in _cmd_contact, sending plain: %s", exc)
            await update.message.reply_text(text)

    async def _cmd_resources(self, update: Update, context) -> None:
        if not update.message:
            return
        args = context.args or []
        resources = self.kb.resources
        if args:
            query = " ".join(args).lower()
            resources = {k: v for k, v in resources.items() if query in k.lower()}
        if not resources:
            available = ", ".join(self.kb.resources.keys())
            await update.message.reply_text(
                f"No resources found for that category.\n\nAvailable: {available}"
            )
            return
        lines = []
        for cat, items in list(resources.items())[:5]:
            lines.append(f"*{cat.title()}*")
            for item in items[:4]:
                line = f"• {item.title}"
                if item.url:
                    line += f": {item.url}"
                lines.append(line)
            lines.append("")
        text = "\n".join(lines).strip()
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Markdown parse failed in _cmd_resources, sending plain: %s", exc)
            await update.message.reply_text(text)

    async def _cmd_help(self, update: Update, context) -> None:
        if not update.message:
            return
        text = (
            "*GSA Gateway — Telegram Bot*\n\n"
            "I answer questions about NJIT's Graduate Student Association.\n\n"
            "*Commands:*\n"
            "/events — Upcoming GSA events\n"
            "/contact [role] — Find GSA officers\n"
            "/resources [category] — Campus resources\n"
            "/help — This message\n\n"
            "Or just type your question naturally!"
        )
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Markdown parse failed in _cmd_help, sending plain: %s", exc)
            await update.message.reply_text(text)

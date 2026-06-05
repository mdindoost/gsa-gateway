"""Telegram platform connector."""

from __future__ import annotations

import asyncio
import difflib
import logging
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from telegram.ext import MessageHandler as PTBHandler
from telegram.ext import filters

from bot.connectors.base import BasePlatform
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.90
_FEEDBACK_TTL = 259200  # 72 hours in seconds


class TelegramConnector(BasePlatform):
    def __init__(
        self, token: str, handler: MessageHandler, kb: KnowledgeBase
    ) -> None:
        self.token = token
        self.handler = handler
        self.kb = kb
        self.app: Optional[Application] = None
        self._stop_event: Optional[asyncio.Event] = None
        # {question_id: {"user_id": int, "timestamp": float,
        #                "question_text": str, "answer_text": str}}
        self._pending_feedback: dict[int, dict] = {}

    # ── Keyboard helpers ──────────────────────────────────────────────────────

    def _build_feedback_keyboard(
        self, question_id: int
    ) -> Optional[InlineKeyboardMarkup]:
        """Build the 👍/👎/🔄 inline keyboard.  Returns None if any callback
        data would exceed Telegram's 64-byte limit."""
        cb_up    = f"fb:{question_id}:up"
        cb_down  = f"fb:{question_id}:down"
        cb_retry = f"fb:{question_id}:retry"

        for cb in (cb_up, cb_down, cb_retry):
            if len(cb.encode()) > 64:
                logger.warning(
                    "Callback data exceeds 64 bytes, skipping keyboard: %s", cb
                )
                return None

        return InlineKeyboardMarkup([[
            InlineKeyboardButton("👍 Helpful",     callback_data=cb_up),
            InlineKeyboardButton("👎 Not helpful", callback_data=cb_down),
            InlineKeyboardButton("🔄 Try again",   callback_data=cb_retry),
        ]])

    def _build_detail_keyboard(self, question_id: int) -> InlineKeyboardMarkup:
        """Build the Wrong info / Incomplete / Off topic follow-up keyboard."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Wrong info",  callback_data=f"fbd:{question_id}:wrong_info"),
            InlineKeyboardButton("Incomplete",  callback_data=f"fbd:{question_id}:incomplete"),
            InlineKeyboardButton("Off topic",   callback_data=f"fbd:{question_id}:off_topic"),
        ]])

    def _register_pending(
        self,
        question_id: int,
        user_id: int,
        question_text: str,
        answer_text: str,
    ) -> None:
        self._pending_feedback[question_id] = {
            "user_id": user_id,
            "timestamp": time.monotonic(),
            "question_text": question_text,
            "answer_text": answer_text,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_services(self) -> None:
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(PTBHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self.app.add_handler(CommandHandler("start",     self._cmd_help))
        self.app.add_handler(CommandHandler("help",      self._cmd_help))
        self.app.add_handler(CommandHandler("events",    self._cmd_events))
        self.app.add_handler(CommandHandler("contact",   self._cmd_contact))
        self.app.add_handler(CommandHandler("resources", self._cmd_resources))
        self.app.add_handler(
            CallbackQueryHandler(self._on_feedback,        pattern=r"^fb:\d+:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._on_feedback_detail, pattern=r"^fbd:\d+:")
        )

    async def start(self) -> None:
        assert self.app is not None, "Call setup_services() before start()"
        self._stop_event = asyncio.Event()
        async with self.app:
            await self.app.start()
            await self.app.updater.start_polling()
            asyncio.create_task(self._cleanup_pending_feedback())
            logger.info("Telegram bot polling — press Ctrl+C to stop")
            try:
                await self._stop_event.wait()
            finally:
                await self.app.updater.stop()
                await self.app.stop()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    # ── Feedback cleanup ──────────────────────────────────────────────────────

    async def _cleanup_pending_feedback(self) -> None:
        """Remove _pending_feedback entries older than 72 hours (checked hourly)."""
        while True:
            await asyncio.sleep(3600)
            now = time.monotonic()
            expired = [
                qid for qid, data in list(self._pending_feedback.items())
                if now - data["timestamp"] > _FEEDBACK_TTL
            ]
            for qid in expired:
                self._pending_feedback.pop(qid, None)
            if expired:
                logger.debug("Cleaned up %d expired Telegram feedback entries", len(expired))

    # ── Message handler ───────────────────────────────────────────────────────

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

        keyboard: Optional[InlineKeyboardMarkup] = None
        if resp.question_id:
            keyboard = self._build_feedback_keyboard(resp.question_id)
            if keyboard:
                self._register_pending(
                    question_id=resp.question_id,
                    user_id=update.effective_user.id,
                    question_text=update.message.text,
                    answer_text=resp.text,
                )

        try:
            await update.message.reply_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )
        except Exception:
            await update.message.reply_text(text, reply_markup=keyboard)

    # ── Feedback callbacks ────────────────────────────────────────────────────

    async def _on_feedback(self, update: Update, context) -> None:
        """Handle 👍 / 👎 / 🔄 button presses."""
        query = update.callback_query
        if not query or not query.data or not query.from_user or not query.message:
            return

        # Parse: fb:{question_id}:{rating}
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer()
            return
        _, qid_str, rating = parts
        try:
            question_id = int(qid_str)
        except ValueError:
            await query.answer()
            return

        # Ownership check
        pending = self._pending_feedback.get(question_id)
        if pending is None or pending["user_id"] != query.from_user.id:
            await query.answer(
                "These buttons are for the person who asked the question.",
                show_alert=True,
            )
            return

        db = self.handler.db

        if rating == "up":
            if db:
                db.log_feedback_rating(
                    question_id=question_id,
                    user_id=query.from_user.id,
                    rating="thumbs_up",
                    platform="telegram",
                )
            self._pending_feedback.pop(question_id, None)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("Thanks for the feedback! 👍")

        elif rating == "down":
            if db:
                db.log_feedback_rating(
                    question_id=question_id,
                    user_id=query.from_user.id,
                    rating="thumbs_down",
                    platform="telegram",
                )
            self._pending_feedback.pop(question_id, None)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer()
            await query.message.reply_text(
                "What was wrong with the answer?",
                reply_markup=self._build_detail_keyboard(question_id),
            )

        elif rating == "retry":
            question_text = pending.get("question_text", "")
            answer_text   = pending.get("answer_text", "")
            self._pending_feedback.pop(question_id, None)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer()

            thinking_msg = await query.message.reply_text(
                "🔄 Trying a different approach..."
            )

            req = MessageRequest(
                user_id=str(query.from_user.id),
                text=question_text,
                platform="telegram",
            )
            try:
                new_resp = await self.handler.retry_question(req)
            except Exception as exc:
                logger.error("Telegram retry error: %s", exc)
                try:
                    await thinking_msg.delete()
                except Exception:
                    pass
                await query.message.reply_text(
                    "Something went wrong with the retry. "
                    "Please try asking again or contact gsa-vpa@njit.edu"
                )
                return

            # Log regenerate linking back to original
            if new_resp.question_id and db:
                db.log_feedback_rating(
                    question_id=new_resp.question_id,
                    user_id=query.from_user.id,
                    rating="regenerate",
                    platform="telegram",
                    original_question_id=question_id,
                )

            # Similarity check
            similarity = difflib.SequenceMatcher(
                None, answer_text, new_resp.text or ""
            ).ratio()

            try:
                await thinking_msg.delete()
            except Exception:
                pass

            if similarity > _SIMILARITY_THRESHOLD or not new_resp.text:
                await query.message.reply_text(
                    "I got the same answer. Try rephrasing your question or "
                    "contact gsa-vpa@njit.edu for direct help."
                )
                return

            new_text = new_resp.text
            if new_resp.source_note:
                new_text += f"\n\n_Source: {new_resp.source_note}_"

            new_keyboard: Optional[InlineKeyboardMarkup] = None
            if new_resp.question_id:
                new_keyboard = self._build_feedback_keyboard(new_resp.question_id)
                if new_keyboard:
                    self._register_pending(
                        question_id=new_resp.question_id,
                        user_id=query.from_user.id,
                        question_text=question_text,
                        answer_text=new_resp.text,
                    )

            try:
                await query.message.reply_text(
                    new_text, parse_mode="Markdown", reply_markup=new_keyboard
                )
            except Exception:
                await query.message.reply_text(new_text, reply_markup=new_keyboard)

        else:
            await query.answer()

    async def _on_feedback_detail(self, update: Update, context) -> None:
        """Handle Wrong info / Incomplete / Off topic button presses."""
        query = update.callback_query
        if not query or not query.data or not query.from_user:
            return

        # Parse: fbd:{question_id}:{detail}
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer()
            return
        _, qid_str, detail = parts
        try:
            question_id = int(qid_str)
        except ValueError:
            await query.answer()
            return

        db = self.handler.db
        if db:
            db.log_feedback_rating(
                question_id=question_id,
                user_id=query.from_user.id,
                rating="thumbs_down",
                platform="telegram",
                detail=detail,
            )

        await query.answer()
        await query.edit_message_text("✅ Feedback recorded — thanks! 🙏")

    # ── Command handlers ──────────────────────────────────────────────────────

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
            query_str = " ".join(args).lower()
            resources = {k: v for k, v in resources.items() if query_str in k.lower()}
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
            "Or just type your question naturally!\n\n"
            "_Tip: DM this bot for a private feedback experience after each answer._"
        )
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Markdown parse failed in _cmd_help, sending plain: %s", exc)
            await update.message.reply_text(text)

"""Telegram platform connector."""

from __future__ import annotations

import asyncio
import difflib
import io
import logging
import time
from typing import Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from telegram.ext import MessageHandler as PTBHandler
from telegram.ext import filters

from bot.connectors.base import BasePlatform
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.knowledge_base import KnowledgeBase
from v2.core.judging.session import JudgingSessionManager

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.90
_FEEDBACK_TTL = 259200  # 72 hours in seconds


class TelegramConnector(BasePlatform):
    def __init__(
        self, token: str, handler: MessageHandler, kb: KnowledgeBase,
        judging_manager: Optional[JudgingSessionManager] = None,
    ) -> None:
        self.token = token
        self.handler = handler
        self.kb = kb
        self.judging_manager = judging_manager
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
        # All-conversational: only /start (welcome) and /qrcode (a generative tool)
        # remain. Everything that used to be /help /events /contact /resources is
        # answered by asking the bot naturally (the message handler above).
        self.app.add_handler(CommandHandler("start",  self._cmd_start))
        self.app.add_handler(CommandHandler("qrcode", self._cmd_qrcode))
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
            # Populate the Telegram command menu (the blue "/" list). The handlers
            # work without this, but the commands stay invisible until registered.
            try:
                await self.app.bot.set_my_commands([
                    BotCommand("start",  "Welcome + how to use the bot"),
                    BotCommand("qrcode", "Generate a GSA-branded QR code"),
                ])
                logger.info("Telegram command menu registered (/start, /qrcode)")
            except Exception as exc:  # noqa: BLE001 - menu is cosmetic; never block startup
                logger.warning("Could not set Telegram command menu: %s", exc)
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

        # Judging intercept — fires before normal RAG so judge messages never
        # reach the knowledge-base handler.
        if self.judging_manager:
            response_text, consumed = self.judging_manager.handle(
                str(update.effective_user.id), update.message.text
            )
            if consumed:
                if response_text:
                    try:
                        await update.message.reply_text(response_text, parse_mode="Markdown")
                    except Exception:  # noqa: BLE001
                        await update.message.reply_text(response_text)
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

    async def _cmd_start(self, update: Update, context) -> None:
        """Welcome / entry point. No command menu — the bot is conversational."""
        if not update.message:
            return
        text = (
            "*GSA Gateway*\n\n"
            "Hi! I'm the Graduate Student Association assistant. Just ask me anything "
            "in plain language — officers, events, funding, travel awards, how to get "
            "involved, campus resources, and more.\n\n"
            "You can also use */qrcode <link or text>* to generate a GSA-branded QR code "
            "(black by default — add `red` for the NJIT-red version).\n\n"
            "_Tip: DM me for a private feedback experience after each answer._"
        )
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Markdown parse failed in _cmd_start, sending plain: %s", exc)
            await update.message.reply_text(text)

    async def _cmd_qrcode(self, update: Update, context) -> None:
        """/qrcode <url or text> [black|red] — branded GSA QR code (default Black & White)."""
        if not update.message:
            return
        from bot.services.qr import MAX_QR_INPUT, build_pair
        # Optional color keyword (leading or trailing). Default is black; the user
        # can opt into NJIT red. Mirrors the Discord /qrcode style choice.
        args = list(context.args or [])
        black = True
        if args and args[-1].lower() in ("black", "red"):
            black = args.pop().lower() == "black"
        elif args and args[0].lower() in ("black", "red"):
            black = args.pop(0).lower() == "black"
        content = " ".join(args).strip()
        if not content:
            await update.message.reply_text(
                "Send the link or text to encode, e.g. `/qrcode https://gsanjit.com`\n"
                "Add `red` or `black` to choose the color (default is black), "
                "e.g. `/qrcode https://gsanjit.com red`",
                parse_mode="Markdown")
            return
        if len(content) > MAX_QR_INPUT:
            await update.message.reply_text(
                f"Input too long — keep it under {MAX_QR_INPUT} characters "
                f"(yours is {len(content)}).")
            return
        try:
            branded, transparent = await asyncio.to_thread(build_pair, content, black=black)
        except Exception:  # noqa: BLE001
            logger.exception("Telegram QR generation failed for content=%r", content[:60])
            await update.message.reply_text(
                "Something went wrong generating the QR code. Please try again.")
            return
        style_label = "Black & White" if black else "Red & White"
        branded_io = io.BytesIO(branded); branded_io.name = "qr_branded.png"
        transparent_io = io.BytesIO(transparent); transparent_io.name = "qr_transparent.png"
        await update.message.reply_photo(
            photo=branded_io,
            caption=f"QR code ({style_label}) for: {content[:80]}{'…' if len(content) > 80 else ''}")
        await update.message.reply_document(
            document=transparent_io,
            caption="Transparent version — paste onto flyers & slides.")

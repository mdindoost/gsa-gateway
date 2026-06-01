"""Free-form conversation handler — responds to natural language in #ask-gsa and DMs."""

import logging
import random
from typing import Optional

import discord
from discord.ext import commands

from bot.services.database import hash_user_id
from bot.services.food_detector import format_food_response, get_food_events
from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FOOD,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_QUESTION,
    INTENT_SOCIAL,
    INTENT_STATEMENT,
    INTENT_THANKS,
)
from bot.services.retriever import SOURCE_FRIENDLY_NAMES

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_rgb(204, 0, 0)


class ChatCog(commands.Cog, name="Chat"):
    """Handles free-form conversation in #ask-gsa channel and DMs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = getattr(bot, "config", None)
        self.retriever = getattr(bot, "retriever", None)
        self.ollama = getattr(bot, "ollama", None)
        self.conversation_manager = getattr(bot, "conversation_manager", None)
        self.intent_detector = getattr(bot, "intent_detector", None)
        self.db = getattr(bot, "db", None)
        self.rate_limiter = getattr(bot, "rate_limiter", None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # GATE 1 — Ignore bots
        if message.author.bot:
            return

        # GATE 2 — Ignore slash command interactions
        if message.content.startswith("/"):
            return

        # GATE 3 — Determine if bot should respond
        channel_name = getattr(message.channel, "name", "DM")
        is_dm = isinstance(message.channel, discord.DMChannel)
        bot_user = self.bot.user
        bot_was_mentioned = (bot_user in message.mentions) if bot_user else False

        ask_gsa_channel = (
            self.config.ask_gsa_channel if self.config else "ask-gsa"
        )

        if self.intent_detector:
            should_respond = self.intent_detector.should_respond(
                message=message.content,
                channel_name=channel_name,
                bot_was_mentioned=bot_was_mentioned,
                ask_gsa_channel=ask_gsa_channel,
            )
        else:
            should_respond = channel_name == ask_gsa_channel or bot_was_mentioned

        if not should_respond and not is_dm:
            return

        # GATE 4 — Rate limiting
        user_id = str(message.author.id)
        if self.rate_limiter and not self.rate_limiter.is_allowed(user_id):
            remaining = getattr(self.rate_limiter, "get_retry_after", lambda _: 30)(user_id)
            await message.reply(
                f"⏳ You're sending messages too quickly. "
                f"Please wait {int(remaining)} seconds.",
                mention_author=False,
            )
            return

        # GATE 5 — Clean message
        bot_mention = f"<@{bot_user.id}>" if bot_user else ""
        if self.intent_detector:
            clean_text = self.intent_detector.clean_message(
                message.content,
                bot_mention_string=bot_mention,
            )
        else:
            clean_text = message.content.replace(bot_mention, "").strip()

        if not clean_text:
            return

        # GATE 6 — Detect intent
        if self.intent_detector:
            intent, confidence = self.intent_detector.detect(clean_text)
        else:
            intent, confidence = INTENT_QUESTION, 0.9

        # ── Handle non-RAG intents ────────────────────────────────────────────

        if intent == INTENT_CLEAR_HISTORY:
            if self.conversation_manager:
                self.conversation_manager.clear_session(user_id)
            await message.reply(
                "🔄 Conversation cleared! Starting fresh. "
                "What would you like to know about GSA?",
                mention_author=False,
            )
            return

        if intent == INTENT_GREETING:
            session = (
                self.conversation_manager.get_session(user_id)
                if self.conversation_manager else None
            )
            if session and len(session.turns) > 0:
                response = (
                    "👋 Welcome back! What else can I help you with?\n"
                    "_(Type 'clear' to start a new conversation)_"
                )
            else:
                response = (
                    "👋 Hi! I'm **GSA Gateway**, NJIT's Graduate "
                    "Student Association AI assistant.\n\n"
                    "I can help you with:\n"
                    "- GSA events and announcements\n"
                    "- Travel awards and funding\n"
                    "- Club financial rules\n"
                    "- Officer contacts\n"
                    "- GSA constitution and policies\n"
                    "- Campus resources\n\n"
                    "Just ask me anything! For example:\n"
                    "_\"How do I apply for a travel award?\"_\n"
                    "_\"What are the penalties for clubs?\"_\n"
                    "_\"Who is the GSA president?\"_"
                )
            await message.reply(response, mention_author=False)
            return

        if intent == INTENT_THANKS:
            responses = [
                "You're welcome! Let me know if you have more questions about GSA. 😊",
                "Happy to help! Feel free to ask anything else about GSA services.",
                "Glad I could help! Don't hesitate to ask if you need more information.",
            ]
            await message.reply(random.choice(responses), mention_author=False)
            return

        if intent == INTENT_HELP:
            await message.reply(
                "Here's how to use GSA Gateway:\n\n"
                "**In #ask-gsa or via DM:**\n"
                "Just type your question naturally — no commands needed!\n\n"
                "**In other channels:**\n"
                "Mention me: @GSA Gateway your question here\n\n"
                "**Slash commands:**\n"
                "- `/events` — see upcoming events\n"
                "- `/contact [role]` — find GSA contacts\n"
                "- `/resources [category]` — campus resources\n"
                "- `/initiative` — submit an idea to GSA\n"
                "- `/feedback` — send anonymous feedback\n\n"
                "**Tips:**\n"
                "- Ask follow-up questions naturally\n"
                "- Type 'clear' to reset our conversation\n"
                "- DM me for private questions 🔒",
                mention_author=False,
            )
            return

        # ── RAG pipeline for FOOD, QUESTION, STATEMENT ───────────────────────

        async with message.channel.typing():
            try:
                chunks = []
                food_events: list[dict] = []
                response_text = ""
                source_note: Optional[str] = None
                used_ollama = False

                # STEP 1: Get conversation history
                history: list[dict] = []
                if self.conversation_manager:
                    max_turns = (
                        self.config.conversation_max_turns
                        if self.config else 5
                    )
                    history = self.conversation_manager.get_history(
                        user_id, max_turns=max_turns
                    )

                # STEP 2: Retrieve relevant chunks
                if intent == INTENT_FOOD:
                    if self.retriever:
                        chunks = await self.retriever.retrieve_for_food_query()
                    food_events = get_food_events(
                        kb=getattr(self.bot, "kb", None),
                        db=self.db,
                        days_ahead=7,
                    )
                elif intent == INTENT_SOCIAL:
                    if self.retriever:
                        chunks = await self.retriever.retrieve(
                            query="social events activities networking happy hour graduate students",
                            source_type_filter="event",
                        )
                elif self.retriever:
                    chunks = await self.retriever.retrieve(
                        query=clean_text,
                        conversation_history=history,
                    )

                # STEP 3: Generate answer
                if intent == INTENT_FOOD and food_events:
                    food_embed = format_food_response(food_events)
                    await message.reply(embed=food_embed, mention_author=False)
                    if self.conversation_manager:
                        self.conversation_manager.add_turn(
                            user_id=user_id,
                            role="user",
                            content=clean_text,
                            channel_id=str(message.channel.id),
                        )
                        self.conversation_manager.add_turn(
                            user_id=user_id,
                            role="assistant",
                            content="[Food events listed]",
                            source_files=["events.yml"],
                        )
                    if self.db:
                        self.db.log_question(
                            user_id=int(user_id),
                            question=clean_text,
                            matched_topic="food events",
                            confidence=100.0,
                            guild_id=getattr(message.guild, "id", None),
                        )
                    return

                if chunks and self.ollama:
                    ai_response = await self.ollama.generate_answer(
                        question=clean_text,
                        chunks=chunks,
                        conversation_history=history,
                    )
                    if ai_response:
                        response_text = ai_response
                        source_files = list({c.source_file for c in chunks})
                        source_names = [
                            SOURCE_FRIENDLY_NAMES.get(f, f) for f in source_files[:2]
                        ]
                        source_note = " & ".join(source_names)
                        used_ollama = True
                    else:
                        # Ollama timeout — fallback to best chunk
                        best = chunks[0]
                        response_text = (
                            f"{best.text[:800]}\n\n"
                            "_⚠️ AI is busy right now — here is the raw info from our "
                            "knowledge base. Try again in a moment for a better answer._"
                        )
                        source_note = SOURCE_FRIENDLY_NAMES.get(
                            best.source_file, best.source_file
                        )
                        used_ollama = False
                elif chunks:
                    # Ollama disabled — raw best chunk
                    best = chunks[0]
                    response_text = best.text[:800]
                    source_note = SOURCE_FRIENDLY_NAMES.get(
                        best.source_file, best.source_file
                    )
                    used_ollama = False
                else:
                    # No chunks — true fallback
                    response_text = (
                        "I wasn't able to find specific information about that "
                        "in the GSA knowledge base.\n\n"
                        "For accurate information, please:\n"
                        "- Visit the GSA office at **Campus Center 110A** "
                        "(weekdays 11AM–5PM)\n"
                        "- Email us at **gsa-pres@njit.edu**\n"
                        "- Use `/contact` to find the right officer"
                    )
                    source_note = None
                    used_ollama = False

                # STEP 4: Build Discord embed response
                embed = discord.Embed(color=NJIT_RED)
                if len(response_text) <= 4096:
                    embed.description = response_text
                else:
                    embed.description = response_text[:4093] + "..."

                footer_parts = ["💡 GSA Gateway"]
                if source_note:
                    footer_parts.append(f"Source: {source_note}")
                if used_ollama:
                    footer_parts.append("AI-generated from official GSA docs")
                embed.set_footer(text=" · ".join(footer_parts))

                await message.reply(embed=embed, mention_author=False)

                # STEP 5: Update conversation memory
                if self.conversation_manager:
                    self.conversation_manager.add_turn(
                        user_id=user_id,
                        role="user",
                        content=clean_text,
                        channel_id=str(message.channel.id),
                    )
                    self.conversation_manager.add_turn(
                        user_id=user_id,
                        role="assistant",
                        content=response_text[:500],
                        source_files=[c.source_file for c in chunks],
                    )

                # STEP 6: Log interaction
                if self.db:
                    self.db.log_question(
                        user_id=int(user_id),
                        question=clean_text,
                        matched_topic=chunks[0].section_title if chunks else None,
                        confidence=chunks[0].relevance_score * 100 if chunks else 0.0,
                        guild_id=getattr(message.guild, "id", None),
                    )

            except Exception as exc:
                logger.error("ChatCog on_message error: %s", exc, exc_info=True)
                try:
                    await message.reply(
                        "I encountered an error processing your question. "
                        "Please try again or contact a GSA officer at gsa-pres@njit.edu",
                        mention_author=False,
                    )
                except Exception:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))

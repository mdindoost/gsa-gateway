"""Free-form conversation handler — responds to natural language in #ask-gsa and DMs."""

import logging
import time

import discord
from discord.ext import commands

from bot.core.message_handler import MessageRequest

logger = logging.getLogger(__name__)

NJIT_RED = discord.Color.from_rgb(204, 0, 0)
_OLLAMA_ALERT_COOLDOWN = 3600


class ChatCog(commands.Cog, name="Chat"):
    """Handles free-form conversation in #ask-gsa channel and DMs."""

    _last_ollama_alert: float = 0.0

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = getattr(bot, "config", None)
        self.intent_detector = getattr(bot, "intent_detector", None)
        self.message_handler = getattr(bot, "message_handler", None)

    async def _notify_ollama_down(self, trigger_channel: discord.abc.Messageable) -> None:
        now = time.monotonic()
        if now - ChatCog._last_ollama_alert < _OLLAMA_ALERT_COOLDOWN:
            return
        ChatCog._last_ollama_alert = now
        admin_id = self.config.admin_discord_id if self.config else None
        if not admin_id:
            return
        try:
            user = await self.bot.fetch_user(admin_id)
            channel_ref = getattr(trigger_channel, "mention", str(trigger_channel))
            await user.send(
                f"⚠️ **GSA Gateway — LLM alert**\n"
                f"Ollama did not respond to a student question in {channel_ref}.\n"
                f"The bot fell back to raw KB text.\n\n"
                f"Check with: `systemctl status ollama` or `ollama ps`\n"
                f"Restart: `sudo systemctl restart ollama`\n\n"
                f"_(This alert won't repeat for 1 hour)_"
            )
        except Exception as exc:
            logger.warning("Could not DM admin about Ollama failure: %s", exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # GATE 1 — Ignore bots
        if message.author.bot:
            return

        # GATE 2 — Ignore slash commands
        if message.content.startswith("/"):
            return

        # GATE 3 — Determine if bot should respond
        channel_name = getattr(message.channel, "name", "DM")
        is_dm = isinstance(message.channel, discord.DMChannel)
        bot_user = self.bot.user
        bot_was_mentioned = (bot_user in message.mentions) if bot_user else False
        ask_gsa_channel = self.config.ask_gsa_channel if self.config else "ask-gsa"

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

        # GATE 3.5 — Ignore member-to-member messages
        if not bot_was_mentioned and not is_dm:
            other_mentions = [u for u in message.mentions if u != bot_user]
            bot_mention_str = f"<@{bot_user.id}>" if bot_user else ""
            content_without_bot = message.content.replace(bot_mention_str, "").strip()
            if other_mentions or "<@" in content_without_bot:
                return

        # Clean text (strip bot mention)
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

        # Delegate to MessageHandler
        async with message.channel.typing():
            try:
                req = MessageRequest(
                    user_id=str(message.author.id),
                    text=clean_text,
                    platform="discord",
                    guild_id=getattr(message.guild, "id", None),
                )
                resp = await self.message_handler.handle(req)

                if not resp.text:
                    return

                embed = discord.Embed(color=NJIT_RED)
                if len(resp.text) <= 4096:
                    embed.description = resp.text
                else:
                    embed.description = resp.text[:4093] + "..."

                footer_parts = ["💡 GSA Gateway"]
                if resp.source_note:
                    footer_parts.append(f"Source: {resp.source_note}")
                if resp.used_ai:
                    footer_parts.append("AI-generated from official GSA docs")
                embed.set_footer(text=" · ".join(footer_parts))

                await message.reply(embed=embed, mention_author=False)

                if resp.ollama_failed:
                    await self._notify_ollama_down(message.channel)

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

"""Slash command: /help — display all available commands."""

import discord
from discord import app_commands
from discord.ext import commands

NJIT_RED = discord.Color.from_str("#CC0000")


class HelpCog(commands.Cog, name="Help"):
    """Handles /help — command reference embed."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Show all available GSA Gateway commands.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        """Return a full command reference embed."""
        embed = discord.Embed(
            title="🎓  GSA Gateway — Command Guide",
            color=NJIT_RED,
            description=(
                "Hello! I'm the NJIT GSA Gateway bot. "
                "Here's everything you can do:"
            ),
        )

        embed.add_field(
            name="📖  Knowledge Base",
            value=(
                "`/ask <question>` — Search GSA's knowledge base for answers about "
                "graduate life, resources, policies, and more."
            ),
            inline=False,
        )
        embed.add_field(
            name="📅  Events",
            value=(
                "`/events` — List all upcoming GSA events.\n"
                "`/event <name>` — Get full details for a specific event."
            ),
            inline=False,
        )
        embed.add_field(
            name="💡  Student Initiatives",
            value=(
                "`/initiative` — Submit an idea or proposal to GSA. "
                "Opens a private form — your identity is kept anonymous unless you choose to share it."
            ),
            inline=False,
        )
        embed.add_field(
            name="💬  Feedback",
            value=(
                "`/feedback <message>` — Send a private, anonymous message "
                "to GSA officers. Suggestions, concerns, and compliments welcome!"
            ),
            inline=False,
        )
        embed.add_field(
            name="📚  Resources",
            value=(
                "`/resources` — Browse available resource categories.\n"
                "`/resources <category>` — Show resources in a specific area "
                "(academic, funding, wellness, international, research, housing, "
                "transportation, campus_life)."
            ),
            inline=False,
        )
        embed.add_field(
            name="📋  Directory",
            value=(
                "`/contact` — List all GSA officers and campus offices.\n"
                "`/contact <role>` — Get contact details for a specific role."
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒  Privacy",
            value=(
                "Your Discord ID is **never stored in plain text**. "
                "All identifiers are hashed with SHA-256. "
                "Contact info is only stored when you explicitly opt in via `/initiative`."
            ),
            inline=False,
        )
        embed.set_footer(
            text="GSA Gateway · NJIT Graduate Student Association · Questions? /ask or /contact"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point."""
    await bot.add_cog(HelpCog(bot))

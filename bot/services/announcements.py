"""Announcement embed formatter for GSA events."""

from typing import Union

import discord

from bot.services.knowledge_base import Event

_GREEN  = discord.Color.from_str("#00AA00")
_BLUE   = discord.Color.from_str("#0055AA")
_ORANGE = discord.Color.from_str("#FF8800")
_RED    = discord.Color.from_str("#CC0000")


def _get(event: Union[Event, dict], key: str, default: str = "") -> str:
    """Unified attribute accessor for Event dataclass or dict."""
    if isinstance(event, dict):
        return str(event.get(key, default))
    return str(getattr(event, key, default))


def format_event_announcement(
    event: Union[Event, dict],
    announcement_type: str,
) -> discord.Embed:
    """Return a rich embed for a GSA event announcement.

    announcement_type: "new" | "reminder_7d" | "reminder_1d" | "reminder_1h"
    """
    name      = _get(event, "name", "GSA Event")
    date_val  = _get(event, "date")
    time_val  = _get(event, "time", "TBD") or "TBD"
    location  = _get(event, "location", "TBD") or "TBD"
    desc      = _get(event, "description", "")
    organizer = _get(event, "organizer", "GSA")
    rsvp      = _get(event, "rsvp_link", "")

    if announcement_type == "new":
        embed = discord.Embed(title=f"📅 NEW EVENT: {name}", color=_GREEN)
        embed.add_field(name="Date",     value=date_val,  inline=True)
        embed.add_field(name="Time",     value=time_val,  inline=True)
        embed.add_field(name="Location", value=location,  inline=True)
        if desc:
            embed.add_field(name="Description", value=desc[:500], inline=False)
        embed.add_field(name="Organizer", value=organizer, inline=True)
        if rsvp:
            embed.add_field(name="RSVP / Info", value=rsvp, inline=True)
        embed.set_footer(text="Add to your calendar • GSA Gateway")

    elif announcement_type == "reminder_7d":
        embed = discord.Embed(title=f"📅 Coming Next Week: {name}", color=_BLUE)
        embed.add_field(name="Date",     value=date_val, inline=True)
        embed.add_field(name="Time",     value=time_val, inline=True)
        embed.add_field(name="Location", value=location, inline=True)
        if desc:
            embed.add_field(name="About", value=desc[:300], inline=False)
        if rsvp:
            embed.add_field(name="RSVP / Info", value=rsvp, inline=False)
        embed.set_footer(text="7 days away • GSA Gateway")

    elif announcement_type == "reminder_1d":
        embed = discord.Embed(title=f"⏰ Tomorrow: {name}", color=_ORANGE)
        embed.add_field(name="Date",     value=date_val, inline=True)
        embed.add_field(name="Time",     value=time_val, inline=True)
        embed.add_field(name="Location", value=location, inline=True)
        if rsvp:
            embed.add_field(name="RSVP / Info", value=rsvp, inline=False)
        embed.set_footer(text="Don't forget! • GSA Gateway")

    elif announcement_type == "reminder_1h":
        embed = discord.Embed(title=f"🔔 Starting Soon: {name}", color=_RED)
        embed.add_field(name="Time",     value=time_val, inline=True)
        embed.add_field(name="Location", value=location, inline=True)
        if rsvp:
            embed.add_field(name="RSVP / Info", value=rsvp, inline=False)
        embed.set_footer(text="Starting in 1 hour • GSA Gateway")

    else:
        embed = discord.Embed(title=f"📅 {name}", color=_GREEN)

    return embed

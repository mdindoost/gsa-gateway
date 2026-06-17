"""Food event detection for the /ask command.

Detects food-related queries and returns upcoming GSA events that involve food.
"""

from datetime import date, timedelta

import discord

FOOD_KEYWORDS = [
    "food", "free food", "snacks", "lunch",
    "dinner", "breakfast", "eat", "eating",
    "hungry", "pizza", "coffee", "drinks",
    "refreshments", "catering", "meal",
    "free lunch", "free snacks", "food today",
    "food this week", "any food", "feeding",
]


# Questions that mention "food" but are about BUDGET / POLICY (not "where's the free food").
# These must go to RAG (the financial bylaws), never to the free-food-events path.
_FOOD_POLICY_GUARD = (
    "cost", "budget", "limit", "spend", "spending", "reimburs", "per person", "per-person",
    "allowance", "maximum", "policy", "bylaw", "how much", "allowed", "cap ", "caps",
)


def is_food_query(query: str) -> bool:
    """True if the query asks about (free) food at GSA events — NOT food budget/policy."""
    q = query.strip().lower()
    if any(g in q for g in _FOOD_POLICY_GUARD):
        return False
    return any(kw in q for kw in FOOD_KEYWORDS)


def _event_has_food(event: dict) -> bool:
    """Return True if an event involves food based on category, name, or description."""
    category = str(event.get("category", "")).lower()
    if category in ("food", "social"):
        return True
    name = str(event.get("name", "")).lower()
    desc = str(event.get("description", "")).lower()
    text = f"{name} {desc}"
    return any(kw in text for kw in FOOD_KEYWORDS)


def get_food_events(kb=None, db=None, days_ahead: int = 7) -> list[dict]:
    """Return upcoming events (within days_ahead) that involve food.

    Checks KB (YAML events) first, then SQLite DB events.
    Deduplicates by name — KB takes precedence.
    """
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events: list[dict] = []
    seen_names: set[str] = set()

    # From knowledge base (YAML events)
    if kb is not None:
        for ev in kb.events:
            try:
                ev_date = date.fromisoformat(str(ev.date))
            except ValueError:
                continue
            if today <= ev_date <= cutoff:
                event_dict = {
                    "name":        ev.name,
                    "date":        str(ev.date),
                    "time":        ev.time,
                    "location":    ev.location,
                    "description": ev.description,
                    "category":    ev.category,
                    "rsvp_link":   ev.rsvp_link,
                }
                if _event_has_food(event_dict):
                    seen_names.add(ev.name)
                    events.append(event_dict)

    # From SQLite DB
    if db is not None:
        for ev in db.get_upcoming_events_db(days=days_ahead):
            if ev["name"] in seen_names:
                continue
            if _event_has_food(dict(ev)):
                seen_names.add(ev["name"])
                events.append(dict(ev))

    events.sort(key=lambda e: e["date"])
    return events


def format_food_response(food_events: list[dict]) -> discord.Embed:
    """Create a Discord embed listing upcoming food events."""
    today_str = date.today().isoformat()
    today_events   = [e for e in food_events if e["date"] == today_str]
    upcoming_events = [e for e in food_events if e["date"] > today_str]

    if today_events and upcoming_events:
        title = "🍕 Food at GSA Events"
    elif today_events:
        title = "🍕 Free Food Today!"
    else:
        title = "📅 Upcoming Events with Food This Week"

    embed = discord.Embed(title=title, color=discord.Color.from_str("#FF8800"))

    if today_events:
        lines: list[str] = []
        for ev in today_events[:5]:
            lines.append(f"**{ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            if ev.get("description"):
                lines.append(str(ev["description"])[:120].strip())
            lines.append("")
        embed.add_field(
            name="🍕 Today!",
            value="\n".join(lines).strip()[:1024],
            inline=False,
        )

    if upcoming_events:
        lines = []
        for ev in upcoming_events[:5]:
            try:
                d = date.fromisoformat(ev["date"])
                day_str = f"{d.strftime('%A, %b')} {d.day}"
            except ValueError:
                day_str = ev["date"]
            lines.append(f"**{day_str} — {ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            lines.append("")
        embed.add_field(
            name="📅 This Week",
            value="\n".join(lines).strip()[:1024],
            inline=False,
        )

    embed.set_footer(
        text="Follow #gsa-food for instant alerts when new food events are announced 🔔"
    )
    return embed


def format_food_text(food_events: list[dict]) -> str:
    """Format food events as plain Markdown text (platform-agnostic).

    Returns a formatted string suitable for any platform (Discord, Telegram, etc).
    Empty if no events provided.
    """
    if not food_events:
        return "No upcoming food events found this week."

    today_str = date.today().isoformat()
    today_events = [e for e in food_events if e["date"] == today_str]
    upcoming_events = [e for e in food_events if e["date"] > today_str]

    lines = []

    if today_events:
        lines.append("**Free Food Today!**\n")
        for ev in today_events[:5]:
            lines.append(f"**{ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            if ev.get("description"):
                lines.append(str(ev["description"])[:120])
            lines.append("")

    if upcoming_events:
        lines.append("**Upcoming Food Events This Week**\n")
        for ev in upcoming_events[:5]:
            try:
                d = date.fromisoformat(ev["date"])
                day_str = f"{d.strftime('%A, %b')} {d.day}"
            except ValueError:
                day_str = ev["date"]
            lines.append(f"**{day_str} — {ev['name']}**")
            lines.append(f"⏰ {ev['time']} | 📍 {ev['location']}")
            lines.append("")

    return "\n".join(lines).strip()


def format_food_alert_embed(event: dict) -> discord.Embed:
    """Create a '🍕 FREE FOOD ALERT!' embed for posting to #gsa-food."""
    embed = discord.Embed(
        title=f"🍕 FREE FOOD ALERT! — {event.get('name', '')}",
        color=discord.Color.from_str("#FF8800"),
    )
    embed.add_field(name="📅 Date",     value=event.get("date", "TBD"),     inline=True)
    embed.add_field(name="⏰ Time",     value=event.get("time", "TBD"),     inline=True)
    embed.add_field(name="📍 Location", value=event.get("location", "TBD"), inline=True)
    if event.get("description"):
        embed.add_field(name="Details", value=str(event["description"])[:500], inline=False)
    embed.set_footer(text="NJIT Graduate Student Association · GSA Gateway")
    return embed

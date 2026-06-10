"""DailyQuoteSource — a worked example of a content generator on the buffered lane.

This is the reference implementation an admin copies to add their own generator.
It shows the whole contract in one small file:

  1. Decide WHAT to post (here: pick today's quote from a curated list — an admin
     would swap this for an API call, RSS fetch, DB query, whatever).
  2. Build a ``PostDraft`` describing the post.
  3. Return it from ``poll()``.

That's the admin's entire job. A ``SourceRunner`` (or a one-shot call to
``enqueue_post``) takes it from there: the draft is validated at the single
``enqueue_post`` door, written as a ``posts`` row, and the live SchedulerRunner
delivers it through the ConnectorRegistry to Discord + Telegram. Nothing in the
publisher/registry/connectors needs to know this generator exists.

Idempotency: the dedup key is the date, so polling repeatedly within a day
enqueues the quote at most once — the second poll is a no-op dedup hit.
"""
from __future__ import annotations

import datetime
import logging

from v2.core.publishing.sources import PostDraft, PostSource

logger = logging.getLogger(__name__)

# The "data source." An admin would replace this with an API/RSS/DB fetch — the
# only contract is that poll() ends up returning PostDraft objects.
QUOTES: list[dict[str, str]] = [
    {"text": "The expert in anything was once a beginner.", "author": "Helen Hayes"},
    {"text": "It does not matter how slowly you go as long as you do not stop.", "author": "Confucius"},
    {"text": "Success is the sum of small efforts, repeated day in and day out.", "author": "Robert Collier"},
    {"text": "The beautiful thing about learning is that no one can take it away from you.", "author": "B.B. King"},
    {"text": "Research is what I'm doing when I don't know what I'm doing.", "author": "Wernher von Braun"},
    {"text": "If we knew what it was we were doing, it would not be called research.", "author": "Albert Einstein"},
    {"text": "A person who never made a mistake never tried anything new.", "author": "Albert Einstein"},
    {"text": "Discipline is the bridge between goals and accomplishment.", "author": "Jim Rohn"},
    {"text": "The future depends on what you do today.", "author": "Mahatma Gandhi"},
    {"text": "Genius is one percent inspiration and ninety-nine percent perspiration.", "author": "Thomas Edison"},
    {"text": "What we know is a drop, what we don't know is an ocean.", "author": "Isaac Newton"},
    {"text": "Perseverance is not a long race; it is many short races one after another.", "author": "Walter Elliot"},
    {"text": "You don't have to be great to start, but you have to start to be great.", "author": "Zig Ziglar"},
    {"text": "Fall seven times, stand up eight.", "author": "Japanese Proverb"},
    {"text": "An investment in knowledge pays the best interest.", "author": "Benjamin Franklin"},
    {"text": "Strive for progress, not perfection.", "author": "Unknown"},
    {"text": "The roots of education are bitter, but the fruit is sweet.", "author": "Aristotle"},
    {"text": "Do the hard jobs first. The easy jobs will take care of themselves.", "author": "Dale Carnegie"},
    {"text": "Little by little, one travels far.", "author": "J.R.R. Tolkien"},
    {"text": "Energy and persistence conquer all things.", "author": "Benjamin Franklin"},
]


def quote_for(day: datetime.date) -> dict[str, str]:
    """Deterministically pick a quote for a given date (stable, rotates daily)."""
    return QUOTES[day.toordinal() % len(QUOTES)]


def build_quote_draft(org_id: int, day: datetime.date,
                      channels: list[str] | None = None,
                      discord_channel: str | None = None) -> PostDraft:
    """Build the PostDraft for a given day's quote. Shared by the source and any
    one-shot caller (e.g. a manual 'post today's quote now' trigger)."""
    q = quote_for(day)
    content = f"💬 **Quote of the day**\n\n“{q['text']}”\n— *{q['author']}*"
    return PostDraft(
        org_id=org_id,
        content=content,
        type="broadcast",
        channels=channels if channels is not None else ["discord", "telegram"],
        discord_channel=discord_channel,
        source_type="daily_quote",
        # one post per day: re-polling the same day dedups to a no-op.
        # enqueue_post prepends source_type, so the stored key is "daily_quote:<date>".
        dedup_key=day.isoformat(),
        metadata={"author": q["author"], "date": day.isoformat()},
    )


class DailyQuoteSource(PostSource):
    """Poll-style generator: each tick offers today's quote (deduped per day).

    Run it with a SourceRunner, or call ``poll()`` yourself and pass the drafts
    to ``enqueue_post``. ``channels``/``discord_channel`` let the admin route it."""

    name = "daily_quote"

    def __init__(self, org_id: int, channels: list[str] | None = None,
                 discord_channel: str | None = None):
        self.org_id = org_id
        self.channels = channels
        self.discord_channel = discord_channel

    async def poll(self) -> list[PostDraft]:
        today = datetime.date.today()  # server-local; UTC acceptable for a non-critical daily post
        return [build_quote_draft(self.org_id, today,
                                  channels=self.channels,
                                  discord_channel=self.discord_channel)]

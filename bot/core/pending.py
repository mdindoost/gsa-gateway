"""Pending-conversational-action state — a resumable offer/clarify the bot made,
keyed to a user and consumed on the next turn. Pure data; NO v2 imports (the session
layer must not depend on the retrieval layer). Options carry plain skill/args dicts;
the resume site rebuilds a v2 Route."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PendingOption:
    label: str            # human label; also the match target for pick-1-of-N ("John Smith")
    action: str           # "structured" (only executor wired now; kept for the deferred live-search follow-up)
    payload: dict         # structured: {"skill": str, "args": dict}


@dataclass
class PendingAction:
    options: list[PendingOption]
    created_at: datetime

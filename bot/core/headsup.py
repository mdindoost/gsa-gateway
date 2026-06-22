"""High-stakes topic heads-up.

For immigration / billing / funding questions the bot still answers, but appends a one-line
note telling the student to confirm with the authoritative office (rules change and those
offices own them). A small, deterministic seed of the future office-routing (cat-M).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    name: str
    office: str
    patterns: tuple[str, ...]


# Order = priority (first match wins).
TOPICS: tuple[Topic, ...] = (
    Topic("immigration", "Office of Global Initiatives (OGI)",
          ("visa", "i-20", "i20", "cpt", "opt", "sevis", "f-1", "f1 status",
           "work authorization", "immigration")),
    Topic("billing", "Office of the Bursar / Student Accounts",
          ("tuition", "bursar", "billing", "financial hold", "late fee",
           "pay my bill", "payment plan", "refund")),
    Topic("funding", "Office of Graduate Studies or your department",
          ("assistantship", "stipend", "fellowship", "teaching assistant",
           "research assistant", "ta", "ra", "ta position", "ra position",
           "tuition waiver")),
    # EOS / parking — fees, hours, lockout numbers are volatile; point users to the office
    # to self-verify the KB snapshot (re-crawl staleness mitigation). Lowest priority.
    Topic("operations", "NJIT Parking Services / EOS office",
          ("parking", "park", "parking permit", "permit", "shuttle", "campus transportation",
           "lockout", "locksmith", "mailroom", "mail room", "photo id", "photo-id", "id card",
           "visitor parking", "zipcar")),
)

_COMPILED: tuple[tuple[Topic, "re.Pattern[str]"], ...] = tuple(
    (t, re.compile("|".join(r"\b" + re.escape(p) + r"\b" for p in t.patterns), re.I))
    for t in TOPICS
)


def match_topic(question: str) -> Topic | None:
    for topic, rx in _COMPILED:
        if rx.search(question or ""):
            return topic
    return None


def headsup_line(topic: Topic) -> str:
    return (f"⚠️ _This is based on the GSA's knowledge — please confirm with the "
            f"{topic.office}, since these rules can change and they are the official "
            f"authority._")


def apply_headsup(response_text: str, question: str) -> str:
    """Append the heads-up to an answer when the question is a high-stakes topic; else
    return the answer unchanged."""
    topic = match_topic(question)
    return f"{response_text}\n\n{headsup_line(topic)}" if topic else response_text

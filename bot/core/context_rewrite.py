"""Contextual query rewrite — resolve conversation follow-ups into standalone queries.

Backlog #2 / the #1 evidenced 👎 bug: conversation history reaches answer GENERATION but never
RETRIEVAL/ROUTING, so a follow-up like "what is his position" retrieves wrong chunks. This module
provides a deterministic referential GATE (no LLM), a deterministic entity-membership VERIFICATION
(the anti-fab guard against a confident *wrong* rewrite), and an orchestrator that wraps the LLM
rewrite with both. The LLM call itself lives in ollama_client.rewrite_with_context.

Spec: docs/superpowers/specs/2026-06-22-contextual-query-rewrite-design.md
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Unit 1: the deterministic referential gate ────────────────────────────────
# Bare personal pronouns (referential, no antecedent in the message).
_PRONOUNS = re.compile(r"\b(his|her|hers|him|its|their|them|they|he|she)\b", re.I)
# Demonstratives used PRONOMINALLY (sentence-final), not as determiners ("those professors").
_DEMONSTRATIVE = re.compile(r"\b(that|those|this|these)\b\s*[?.!]*\s*$", re.I)
# Elliptical follow-up openers.
_OPENERS = re.compile(r"^\s*(what about|how about|and\b|why not|what else|the official one|the other one)", re.I)


def is_follow_up(message: str) -> bool:
    """True iff the message carries a REFERENTIAL signal (bare pronoun / elliptical opener) — NOT
    mere shortness (a complete terse query like "office hours" must not fire)."""
    m = (message or "").strip()
    if not m:
        return False
    return bool(_OPENERS.search(m) or _PRONOUNS.search(m) or _DEMONSTRATIVE.search(m))


# ── Unit 2: deterministic entity-membership verification (the load-bearing guard) ──
_STOP = {
    "what", "whats", "is", "are", "am", "be", "the", "a", "an", "of", "for", "in", "on", "to",
    "do", "does", "did", "you", "your", "me", "my", "i", "can", "could", "how", "who", "whos",
    "when", "where", "why", "which", "and", "about", "his", "her", "hers", "him", "its", "it",
    "their", "them", "they", "he", "she", "that", "those", "this", "these", "with", "at", "by",
    "from", "as", "or", "not", "didnt", "dont", "please", "pls",
}


def _content_words(s: str) -> set[str]:
    toks = (t.strip("'.-") for t in re.findall(r"[A-Za-z0-9'./-]+", (s or "").lower()))
    return {t for t in toks if t and t not in _STOP}


def _added_entities(s: str) -> list[str]:
    """Proper-noun-ish tokens (capitalized, not sentence-initial), possessive-normalized + lowercased."""
    toks = re.findall(r"[A-Za-z][A-Za-z'.-]*", s or "")
    out = []
    for i, t in enumerate(toks):
        if i == 0:                       # skip the sentence-initial capital
            continue
        if t[0].isupper():
            e = re.sub(r"'s$", "", t, flags=re.I).strip("'.-").lower()
            if e:
                out.append(e)
    return out


def verify_rewrite(original: str, resolved: str, history: str) -> str:
    """Return `resolved` only if it is a SAFE rewrite of `original`; else passthrough `original`.

    Guards (deterministic, no LLM):
      - empty/unchanged → passthrough.
      - balloon (>3x length) → passthrough (intent change / runaway).
      - intent change: every content word of the original must survive in the resolved.
      - entity-membership (THE anti-fab guard): every proper-noun the rewrite ADDED must appear
        literally in the history; a hallucinated antecedent → discard → passthrough.
    """
    original = (original or "").strip()
    resolved = (resolved or "").strip()
    if not resolved or resolved.lower() == original.lower():
        return original
    if len(resolved) > 3 * max(len(original), 1):
        return original
    if not _content_words(original).issubset(_content_words(resolved)):
        return original                  # original's question content didn't survive → intent change
    hist = (history or "").lower()
    orig_entities = set(_added_entities(original))
    for e in _added_entities(resolved):
        if e in orig_entities:
            continue
        if e not in hist:
            logger.warning("context_rewrite: discarded rewrite with unsupported entity %r "
                           "(orig=%r resolved=%r)", e, original, resolved)
            return original              # hallucinated antecedent → passthrough
    return resolved


# ── Unit 3: the rewrite prompt (pure; ollama_client does the HTTP) ────────────
REWRITE_SYSTEM = (
    "You rewrite a student's follow-up message into a single STANDALONE question, using ONLY the "
    "conversation history for context. Rules: (1) Resolve pronouns and ellipsis using ONLY names, "
    "people, departments, or topics that appear in the history — never invent. (2) Do NOT change the "
    "TYPE of question (resolve references only; keep it the same ask). (3) If more than one entity "
    "could be the referent, or you cannot resolve it from the history, return the message UNCHANGED. "
    "Output ONLY the standalone question, nothing else."
)


def build_rewrite_prompt(history: str, message: str) -> tuple[str, str]:
    """Return (system, user) prompts for the contextual rewrite. Pure — unit-testable."""
    user = (
        "Conversation history:\n"
        f"{history}\n\n"
        f"Follow-up message: {message}\n\n"
        "Standalone question:"
    )
    return REWRITE_SYSTEM, user


# ── Unit 4: orchestrator — gate → LLM rewrite → deterministic verify ──────────
def _format_history(turns) -> str:
    lines = []
    for t in (turns or []):
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{t.get('role', '')}: {content}")
    return "\n".join(lines)


async def resolve_query(message: str, history_turns, llm) -> tuple[str, bool]:
    """Resolve a follow-up into a standalone query. Returns (resolved_query, was_rewritten).

    Passthrough the ORIGINAL (was_rewritten=False) on: gate-miss, no history, no llm, an LLM
    failure, or a verify that discards the rewrite. One LLM call only when the gate fires."""
    message = message or ""
    if not is_follow_up(message) or not history_turns or llm is None:
        return message, False
    history_text = _format_history(history_turns)
    try:
        resolved = await llm.rewrite_with_context(history_text, message)
    except Exception:  # noqa: BLE001 - never break the message path; passthrough
        logger.warning("context_rewrite: LLM rewrite failed; passthrough", exc_info=True)
        return message, False
    verified = verify_rewrite(message, resolved or "", history_text)
    rewritten = verified.strip().lower() != message.strip().lower()
    if rewritten:
        logger.info("context_rewrite: %r -> %r", message, verified)
    return verified, rewritten

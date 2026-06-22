"""Contextual query rewrite — fix conversation follow-up accuracy (backlog #2).

Spec: docs/superpowers/specs/2026-06-22-contextual-query-rewrite-design.md
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import asyncio

from bot.core.context_rewrite import is_follow_up, resolve_query, verify_rewrite


def _run(coro):
    return asyncio.run(coro)


class _StubLLM:
    def __init__(self, out):
        self.out = out
        self.calls = 0
        self.seen = None

    async def rewrite_with_context(self, history, message):
        self.calls += 1
        self.seen = (history, message)
        return self.out


_HIST = [
    {"role": "user", "content": "who is Mark Cartwright"},
    {"role": "assistant", "content": "Mark Cartwright is a professor in Informatics."},
]


# ── Unit 1: the deterministic referential gate (no LLM) ───────────────────────
@pytest.mark.parametrize("msg", [
    "what is his position",
    "why you didnt list him",
    "what is her research field",
    "what about for BME?",
    "and the officers?",
    "what about the official one?",
    "how about it",
    "why didn't you list them",
])
def test_followup_signals_fire(msg):
    assert is_follow_up(msg) is True


@pytest.mark.parametrize("msg", [
    "who is the GSA president",
    "office hours",
    "what are the CS faculty",
    "NJIT's parking permit cost",       # possessive naming an entity, not a bare pronoun
    "GSA's officers list",
    "who works on robotics",
    "what is the travel award amount",
])
def test_standalone_does_not_fire(msg):
    assert is_follow_up(msg) is False


# ── Unit 2: deterministic entity-membership verification (the anti-fab guard) ──
def test_rewrite_kept_when_added_entity_in_history():
    # "his position" → resolved adds "Cartwright"; Cartwright IS in history → keep resolved
    history = "user: who is Mark Cartwright\nassistant: Mark Cartwright is a professor in Informatics."
    out = verify_rewrite("what is his position", "what is Mark Cartwright's position", history)
    assert out == "what is Mark Cartwright's position"


def test_rewrite_discarded_when_added_entity_not_in_history():
    # rewrite hallucinated "Vincent Oria" (NOT in history) → discard, passthrough original
    history = "user: who is Mark Cartwright\nassistant: Mark Cartwright is a professor."
    out = verify_rewrite("what is his position", "what is Vincent Oria's position", history)
    assert out == "what is his position"


def test_rewrite_passthrough_when_unchanged():
    history = "user: hi\nassistant: hello"
    out = verify_rewrite("office hours", "office hours", history)
    assert out == "office hours"


def test_rewrite_discarded_when_intent_changed_to_non_question():
    # dropped the interrogative → intent change → discard
    history = "user: who is Mark Cartwright\nassistant: a professor."
    out = verify_rewrite("what is his position", "tell me about Mark Cartwright", history)
    assert out == "what is his position"


# ── Unit 4: orchestrator (gate → LLM rewrite → verify) ────────────────────────
def test_resolve_standalone_skips_llm():
    llm = _StubLLM("should not be used")
    out, rewritten = _run(resolve_query("who is the GSA president", _HIST, llm))
    assert out == "who is the GSA president" and rewritten is False and llm.calls == 0


def test_resolve_followup_good_rewrite():
    llm = _StubLLM("what is Mark Cartwright's position")
    out, rewritten = _run(resolve_query("what is his position", _HIST, llm))
    assert out == "what is Mark Cartwright's position" and rewritten is True and llm.calls == 1


def test_resolve_followup_hallucinated_entity_passthrough():
    llm = _StubLLM("what is Vincent Oria's position")   # Oria NOT in history
    out, rewritten = _run(resolve_query("what is his position", _HIST, llm))
    assert out == "what is his position" and rewritten is False


def test_resolve_no_history_skips_llm():
    llm = _StubLLM("x")
    out, rewritten = _run(resolve_query("what is his position", [], llm))
    assert out == "what is his position" and rewritten is False and llm.calls == 0


def test_resolve_llm_failure_passthrough():
    class _Boom:
        calls = 0
        async def rewrite_with_context(self, h, m):
            raise RuntimeError("ollama down")
    out, rewritten = _run(resolve_query("what is his position", _HIST, _Boom()))
    assert out == "what is his position" and rewritten is False


# ── Unit 3: the rewrite prompt (pure; the HTTP call lives in ollama_client) ────
def test_build_rewrite_prompt_carries_history_and_message():
    from bot.core.context_rewrite import build_rewrite_prompt
    history = "user: who is Mark Cartwright\nassistant: a professor in Informatics."
    sys_p, user_p = build_rewrite_prompt(history, "what is his position")
    assert "Mark Cartwright" in user_p and "what is his position" in user_p
    assert "standalone" in (sys_p + user_p).lower()
    # the rules the reviews required must be present in the instruction
    low = (sys_p + user_p).lower()
    assert "only" in low and "unchanged" in low   # resolve only from history; ambiguity→unchanged

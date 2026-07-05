"""TDD — Gap #2: clarify when a singular-personal pronoun is genuinely UNRESOLVABLE.

The twin of A3: A3 clarifies when the antecedent is AMBIGUOUS (≥2 named); Gap #2 clarifies when it is
UNRESOLVABLE (0 antecedent) — instead of silently dropping the pronoun and answering a generic question.
POST-LLM: only fires on an unresolved passthrough of a BARE singular-personal pronoun.
Spec: docs/superpowers/specs/2026-07-04-gap2-unresolvable-pronoun-clarify-design.md
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import asyncio

import pytest

import bot.config as botcfg
from bot.core.context_rewrite import resolve_query, _bare_singular_pronoun


def _run(coro):
    return asyncio.run(coro)


class _StubLLM:
    """Returns a fixed rewrite. `out=None` → echo the message (simulate 'can't resolve', rule 3)."""
    def __init__(self, out=None):
        self.out, self.calls = out, 0

    async def rewrite_with_context(self, history, message):
        self.calls += 1
        return self.out if self.out is not None else message


@pytest.fixture()
def gap2_on(monkeypatch):
    monkeypatch.setattr(botcfg, "UNRESOLVED_PRONOUN_CLARIFY_ENABLED", True)
    monkeypatch.setattr(botcfg, "ANTECEDENT_GUARD_ENABLED", True)  # prod state
    yield


@pytest.fixture()
def gap2_off(monkeypatch):
    monkeypatch.setattr(botcfg, "UNRESOLVED_PRONOUN_CLARIFY_ENABLED", False)
    monkeypatch.setattr(botcfg, "ANTECEDENT_GUARD_ENABLED", True)
    yield


# a history turn that names NOBODY → the pronoun is unresolvable
NO_PERSON_HIST = [
    {"role": "user", "content": "what is machine learning"},
    {"role": "assistant", "content": "Machine learning is a field of study within AI."},
]


# ══ the bareness helper ══════════════════════════════════════════════════════
def test_bare_pronoun_true_when_pronoun_leads():
    assert _bare_singular_pronoun("is he working on machine learning") is True
    assert _bare_singular_pronoun("what is his position") is True


def test_bare_pronoun_false_with_in_message_antecedent():
    # a content word (proper noun, lowercase or not) before the pronoun → NOT bare
    assert _bare_singular_pronoun("who is Bryan Pfister and what is his h-index") is False
    assert _bare_singular_pronoun("does koutis work with his students") is False


# ══ flag ON — the clarify fires on a genuine unresolvable bare pronoun ═════════
def test_clarify_when_unresolvable(gap2_on):
    llm = _StubLLM(out=None)  # LLM returns message unchanged → can't resolve
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, llm))
    assert rr.clarify_text is not None
    assert '"he"' in rr.clarify_text
    assert rr.rewritten is False


def test_clarify_on_cosmetic_only_rewrite(gap2_on):
    # LIVE-OBSERVED: the real LLM often returns "Is he working on ML?" (capitalization + '?') — a
    # COSMETIC rewrite that leaves the pronoun UNRESOLVED but counts as rewritten=True. The bare pronoun
    # survives in the result → must still clarify (bareness-of-RESULT signal, not rewritten==False).
    llm = _StubLLM(out="Is he working on machine learning?")
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, llm))
    assert rr.clarify_text is not None
    assert '"he"' in rr.clarify_text


def test_no_clarify_on_coreference_after_resolution(gap2_on):
    # "is he working with his students" resolved → "Is Ioannis Koutis working with his students": the
    # residual "his" now has a name (content word) before it → verified NOT bare → no false clarify.
    hist = [{"role": "assistant", "content": "Ioannis Koutis is a professor of computer science."}]
    llm = _StubLLM(out="Is Ioannis Koutis working with his students")
    rr = _run(resolve_query("is he working with his students", hist, llm))
    assert rr.clarify_text is None
    assert rr.rewritten is True


def test_clarify_carries_distinct_reason(gap2_on):
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_reason == "unresolved-antecedent"


def test_clarify_on_no_history_first_message(gap2_on):
    # bare pronoun as the FIRST message (no history), llm present → unresolvable → clarify
    rr = _run(resolve_query("is he working on machine learning", [], _StubLLM(out=None)))
    assert rr.clarify_text is not None


def test_no_clarify_when_llm_none_system_degraded(gap2_on):
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, None))
    assert rr.clarify_text is None
    assert rr.rewritten is False


# ══ flag ON — NO over-clarify (the no-nag guarantees) ═════════════════════════
def test_no_clarify_when_llm_resolves(gap2_on):
    # prose named the person (untagged); the LLM resolves it → rewritten, no clarify
    hist = [{"role": "assistant", "content": "Ioannis Koutis is a professor of computer science."}]
    llm = _StubLLM(out="is Ioannis Koutis working on machine learning")
    rr = _run(resolve_query("is he working on machine learning", hist, llm))
    assert rr.clarify_text is None
    assert rr.rewritten is True


def test_no_clarify_with_in_message_antecedent(gap2_on):
    # self-contained compound query → LLM returns it unchanged → but NOT bare → no nag
    q = "who is Bryan Pfister and what is his h-index"
    rr = _run(resolve_query(q, NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_text is None


def test_no_clarify_lowercase_in_message_antecedent(gap2_on):
    rr = _run(resolve_query("does koutis work with his students", NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_text is None


def test_no_clarify_on_empty_llm_response(gap2_on):
    # empty rewrite = system noise, not "can't resolve" → passthrough, no clarify
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, _StubLLM(out="")))
    assert rr.clarify_text is None


def test_no_clarify_plural_pronoun(gap2_on):
    rr = _run(resolve_query("are they working on machine learning", NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_text is None


def test_no_clarify_non_pronoun_followup(gap2_on):
    rr = _run(resolve_query("what about the deadline", NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_text is None


# ══ A3 precedence — the ≥2-name case keeps A3's candidate-listing clarify ══════
def test_a3_precedence_lists_candidates(gap2_on):
    roster = [{"role": "assistant", "content": "Two profs: Ana Rolim and Bryan Pfister.",
               "person_names": ["Ana Rolim", "Bryan Pfister"]}]
    rr = _run(resolve_query("what is his h-index", roster, _StubLLM(out=None)))
    assert rr.clarify_text is not None
    assert "Ana Rolim" in rr.clarify_text and "Bryan Pfister" in rr.clarify_text  # A3 msg, not Gap #2
    assert rr.clarify_reason == "ambiguous-antecedent"


# ══ flag OFF — literal zero change ════════════════════════════════════════════
def test_flag_off_no_clarify(gap2_off):
    rr = _run(resolve_query("is he working on machine learning", NO_PERSON_HIST, _StubLLM(out=None)))
    assert rr.clarify_text is None
    assert rr.rewritten is False


def test_flag_off_first_message_no_clarify(gap2_off):
    rr = _run(resolve_query("is he working on machine learning", [], _StubLLM(out=None)))
    assert rr.clarify_text is None

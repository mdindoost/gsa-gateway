"""A3 — antecedent-ambiguity guard. Layer 2 (verify_rewrite roster-pick backstop) + layer 1
(ambiguity_clarify pre-LLM gate) + resolve_query flag integration.
Spec: docs/superpowers/specs/2026-07-04-a3-antecedent-ambiguity-design.md"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import asyncio

import bot.config as botcfg
from bot.core.context_rewrite import (
    verify_rewrite, ambiguity_clarify, resolve_query, RewriteResult,
)

ROSTER = ("assistant: 11 faculty work on brain imaging: Ana Rolim, Bharat Biswal, "
          "Bryan Pfister, Elisa Kallioniemi, Xin Di.")


def _run(coro):
    return asyncio.run(coro)


class _StubLLM:
    def __init__(self, out):
        self.out, self.calls = out, 0

    async def rewrite_with_context(self, history, message):
        self.calls += 1
        return self.out


# ══ Layer 2: verify_rewrite roster-pick backstop (guard_enabled=True) ══════════
def test_backstop_blocks_roster_pick():
    # 1-of-5 plucked from a roster → passthrough
    out = verify_rewrite("what is his h-index", "what is Bryan Pfister's h-index",
                         ROSTER, guard_enabled=True)
    assert out == "what is his h-index"


def test_backstop_blocks_sentence_cased_roster_pick():
    # F1: an instruction-tuned LLM sentence-cases its output (leading "What", trailing "?"). The
    # leading capitalized word must NOT inflate the added-name count and bypass the backstop.
    assert verify_rewrite("what is his h-index", "What is Bryan Pfister's h-index?",
                          ROSTER, guard_enabled=True) == "what is his h-index"
    assert verify_rewrite("what is his h-index", "What is the h-index of Bryan Pfister?",
                          ROSTER, guard_enabled=True) == "what is his h-index"


def test_backstop_accepts_single_person_with_capitalized_area_list():
    # R1 witness: a single-person answer whose text has a capitalized AREA list must resolve.
    hist = "assistant: Guiling Wang researches Computer Vision, Machine Learning, Networking."
    out = verify_rewrite("what is his h-index", "what is Guiling Wang's h-index",
                         hist, guard_enabled=True)
    assert out == "what is Guiling Wang's h-index"


def test_backstop_accepts_name_title_appositive():
    # R1 Hole-B witness: "Name, Title …" is a 2-run appositive, NOT a ≥3 list → resolve.
    hist = ("assistant: Bharat Biswal, Distinguished Professor of Biomedical Engineering, "
            "directs the center.")
    out = verify_rewrite("what is his h-index", "what is Bharat Biswal's h-index",
                         hist, guard_enabled=True)
    assert out == "what is Bharat Biswal's h-index"


def test_backstop_accepts_when_standalone_occurrence_exists():
    # R1 Hole-A witness: roster THEN a standalone "Bryan Pfister" mention → the standalone
    # occurrence rescues the pick → resolve.
    hist = (ROSTER + "\nuser: tell me about Bryan Pfister\n"
            "assistant: Bryan Pfister is a professor of biomedical engineering.")
    out = verify_rewrite("what is his h-index", "what is Bryan Pfister's h-index",
                         hist, guard_enabled=True)
    assert out == "what is Bryan Pfister's h-index"


def test_backstop_accepts_resolve_to_set():
    # added ≥2 names = legitimate plural resolution → not a 1-of-N pick.
    out = verify_rewrite("what are their h-indexes",
                         "what are Ana Rolim and Bryan Pfister's h-indexes",
                         ROSTER, guard_enabled=True)
    assert out == "what are Ana Rolim and Bryan Pfister's h-indexes"


def test_backstop_blocks_singular_they_singularized_to_one():
    # number-blind: "their" singularized by the LLM to one roster member → still a pick → block.
    out = verify_rewrite("what is their h-index", "what is Bryan Pfister's h-index",
                         ROSTER, guard_enabled=True)
    assert out == "what is their h-index"


def test_backstop_noop_when_guard_disabled():
    # flag off → old behavior: the roster pick passes (entity is literally in history).
    out = verify_rewrite("what is his h-index", "what is Bryan Pfister's h-index",
                         ROSTER, guard_enabled=False)
    assert out == "what is Bryan Pfister's h-index"


# ══ Layer 1: ambiguity_clarify pre-LLM gate ══════════════════════════════════
def _turn(role, content, names=None):
    return {"role": role, "content": content, "person_names": names or []}


def test_gate_fires_on_two_plus_preceding_people():
    hist = [_turn("user", "brain faculty"),
            _turn("assistant", "11 faculty …", ["Ana Rolim", "Bharat Biswal", "Bryan Pfister"])]
    out = ambiguity_clarify("what is his h-index", hist)
    assert out is not None and "which one" in out.lower()
    assert "Ana Rolim" in out and "Bryan Pfister" in out


def test_gate_silent_on_single_preceding_person():
    hist = [_turn("assistant", "Guiling Wang is a professor.", ["Guiling Wang"])]
    assert ambiguity_clarify("what is his h-index", hist) is None


def test_gate_silent_on_stale_tag_after_untagged_turn():
    # Hole-C: roster tagged, THEN an untagged RAG answer, THEN "his". The immediately-preceding
    # (untagged) turn has no ≥2 tag → do NOT clarify with the stale roster.
    hist = [_turn("assistant", "11 faculty …", ["A", "B", "C", "D", "E"]),
            _turn("user", "who is the dean of students?"),
            _turn("assistant", "The Dean of Students is Marybeth Boger.")]  # untagged RAG
    assert ambiguity_clarify("what is his email", hist) is None


def test_gate_silent_on_plural_pronoun():
    hist = [_turn("assistant", "11 faculty …", ["Ana Rolim", "Bharat Biswal", "Bryan Pfister"])]
    assert ambiguity_clarify("what are their emails", hist) is None


def test_gate_silent_without_pronoun():
    hist = [_turn("assistant", "11 faculty …", ["Ana Rolim", "Bharat Biswal", "Bryan Pfister"])]
    assert ambiguity_clarify("office hours", hist) is None


def test_gate_caps_listed_names_at_five():
    names = ["A", "B", "C", "D", "E", "F", "G"]
    hist = [_turn("assistant", "roster", names)]
    out = ambiguity_clarify("what is his h-index", hist)
    assert out is not None and out.endswith("(or give the full name).") and "…" in out


# ══ resolve_query integration (flag on/off) ═══════════════════════════════════
def test_resolve_query_returns_rewrite_result_type():
    llm = _StubLLM("x")
    rr = _run(resolve_query("office hours", [_turn("assistant", "hi")], llm))
    assert isinstance(rr, RewriteResult) and rr.clarify_text is None


def test_resolve_query_clarifies_when_flag_on(monkeypatch):
    monkeypatch.setattr(botcfg, "ANTECEDENT_GUARD_ENABLED", True)
    llm = _StubLLM("what is Bryan Pfister's h-index")
    hist = [_turn("assistant", "11 faculty …",
                  ["Ana Rolim", "Bharat Biswal", "Bryan Pfister", "Elisa Kallioniemi", "Xin Di"])]
    rr = _run(resolve_query("what is his h-index", hist, llm))
    assert rr.clarify_text is not None
    assert rr.query == "what is his h-index" and rr.rewritten is False
    assert llm.calls == 0                     # LLM skipped entirely


def test_resolve_query_backstop_catches_untagged_prose_roster(monkeypatch):
    # Preceding turn is UNTAGGED (person_names=[]) so layer 1 doesn't fire, but its TEXT holds a
    # ≥3 roster → layer 2 (verify backstop) passes the pick through.
    monkeypatch.setattr(botcfg, "ANTECEDENT_GUARD_ENABLED", True)
    llm = _StubLLM("what is Bryan Pfister's h-index")
    hist = [_turn("user", "brain faculty"),
            _turn("assistant",
                  "11 faculty: Ana Rolim, Bharat Biswal, Bryan Pfister, Elisa Kallioniemi, Xin Di.")]
    rr = _run(resolve_query("what is his h-index", hist, llm))
    assert rr.query == "what is his h-index" and rr.rewritten is False
    assert llm.calls == 1                      # gate silent → LLM ran → backstop discarded


def test_resolve_query_flag_off_preserves_old_wrong_behavior(monkeypatch):
    # flag OFF = zero change: the same roster pick is ACCEPTED (documents the pre-A3 behavior).
    monkeypatch.setattr(botcfg, "ANTECEDENT_GUARD_ENABLED", False)
    llm = _StubLLM("what is Bryan Pfister's h-index")
    hist = [_turn("assistant", "11 faculty …",
                  ["Ana Rolim", "Bryan Pfister", "Xin Di"])]
    # history TEXT must contain the picked name for the entity-membership guard to pass
    hist[0]["content"] = ("11 faculty: Ana Rolim, Bharat Biswal, Bryan Pfister, "
                          "Elisa Kallioniemi, Xin Di.")
    rr = _run(resolve_query("what is his h-index", hist, llm))
    assert rr.clarify_text is None
    assert rr.query == "what is Bryan Pfister's h-index" and rr.rewritten is True

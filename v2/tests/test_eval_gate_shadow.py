"""Tests for the shadow-gate runner's per-question decision loop (scripts/eval_gate_shadow.py).

The loop wires Gate 1 -> gate-the-gate (ce band) -> Gate 2, with retrieval + LLM injected so it is
testable without Ollama or the DB. The full main() runs the real V2Retriever + OllamaClient.
"""
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from eval_gate_shadow import shadow_one


def _never_call(*a, **k):
    raise AssertionError("should not be called")


def _run(coro):
    return asyncio.run(coro)


def test_gate1_hit_short_circuits_retrieval_and_llm():
    rec = _run(shadow_one("what is my account balance", retrieve_fn=_never_call, llm_fn=_never_call))
    assert rec["outcome"] == "deflect" and rec["gate"] == "gate1" and rec["cue"] == "personal"


def test_high_ce_answers_without_calling_llm():
    rec = _run(shadow_one(
        "when is the add/drop deadline",
        retrieve_fn=lambda q: (["Add/drop ends May 1."], 0.95),
        llm_fn=_never_call,
        band=0.70,
    ))
    assert rec["outcome"] == "answer" and rec["gate"] == "ce_high"


def test_low_ce_not_in_context_routes_to_fallback():
    rec = _run(shadow_one(
        "what is the homecoming game score",
        retrieve_fn=lambda q: (["NJIT has many clubs."], 0.20),
        llm_fn=lambda s, u: '{"supporting_quote":"","label":"NOT_IN_CONTEXT","missing_piece":"score"}',
        band=0.70,
    ))
    assert rec["outcome"] == "fallback" and rec["gate"] == "gate2" and rec["label"] == "NOT_IN_CONTEXT"


def test_low_ce_supported_answers():
    rec = _run(shadow_one(
        "what is the late fee",
        retrieve_fn=lambda q: (["The late fee is $250."], 0.40),
        llm_fn=lambda s, u: '{"supporting_quote":"The late fee is $250.","label":"FULLY_SUPPORTED","missing_piece":""}',
        band=0.70,
    ))
    assert rec["outcome"] == "answer" and rec["gate"] == "gate2"

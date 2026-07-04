"""TDD — MessageResponse.is_abstain / abstain_reason (Fable-accepted bot-side change 2026-07-04).

A structured signal for "this response is a canned non-answer" so the eval harness (and anything else)
can detect abstentions without brittle answer-text markers. Tag-at-source only — NEVER a heuristic
(is_deflection/looks_like_deflection stays untouched). Additive field; connectors ignore it.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.config as botcfg
from bot.core.message_handler import (
    MessageHandler, MessageRequest, MessageResponse,
    _KB_MISS_RESPONSE, _CLARIFY_MSG, _RAG_ERROR_RESPONSE,
)
from bot.core.live_query import LIVE_NOT_FOUND_MSG
from bot.services.intent_detector import INTENT_QUESTION


# ═══════════════ compat invariant (connector safety) ═══════════════
def test_defaults_are_answer_not_abstain():
    r = MessageResponse(text="anything")
    assert r.is_abstain is False and r.abstain_reason is None


def test_rag_error_constant_matches_prior_literal():
    # the hoist must be byte-identical to the old inline text (no wording drift)
    assert _RAG_ERROR_RESPONSE == (
        "I encountered an error processing your question. "
        "Please try again or contact a GSA officer at gsa-pres@njit.edu")


# ═══════════════ eval now keys off the FLAG, not the canned text ═══════════════
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location(
    "eval_run", Path(__file__).resolve().parents[2] / "scripts" / "eval_run.py")
eval_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_run)


@pytest.mark.parametrize("canned", [_KB_MISS_RESPONSE, _CLARIFY_MSG, LIVE_NOT_FOUND_MSG, _RAG_ERROR_RESPONSE])
def test_canned_text_with_flag_is_deflect(canned):
    # is_abstain=True (what the handler tags at source) → deflect, no text-marker coupling
    assert eval_run.classify(canned, is_live=False, is_abstain=True) == "deflect"


@pytest.mark.parametrize("canned", [_KB_MISS_RESPONSE, _CLARIFY_MSG, LIVE_NOT_FOUND_MSG, _RAG_ERROR_RESPONSE])
def test_canned_text_without_flag_is_not_special_cased(canned):
    # the coupling is gone: the same text WITHOUT the flag is just a kb answer to the classifier
    assert eval_run.classify(canned, is_live=False, is_abstain=False) == "kb"


# ═══════════════ handler-driven set-sites ═══════════════
def _make_chunk(text="The GSA travel award is $500 for domestic conferences."):
    c = MagicMock()
    c.text = text
    c.source_file = "gsa_faq.md"
    c.section_title = "Travel Award"
    c.relevance_score = 0.8
    return c


def _make_handler(ollama=None, *, chunks=None, retrieve_raises=False):
    rate_limiter = MagicMock(); rate_limiter.is_allowed.return_value = True
    intent_detector = MagicMock(); intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    cm = MagicMock()
    cm.get_session.return_value = None
    cm.get_history.return_value = []
    cm.get_mode.return_value = "gsa"
    config = MagicMock(); config.conversation_max_turns = 5
    retriever = AsyncMock()
    retriever.top_relevance = MagicMock(return_value=0.9)
    if retrieve_raises:
        retriever.retrieve = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        retriever.retrieve = AsyncMock(return_value=chunks if chunks is not None else [_make_chunk()])
    retriever.corpus_ready = MagicMock(return_value=False)
    if ollama is not None:
        ollama.prefit = MagicMock(side_effect=lambda q, ch, h=None: ch)
    return MessageHandler(retriever=retriever, ollama=ollama, conversation_manager=cm,
                          intent_detector=intent_detector, db=MagicMock(), rate_limiter=rate_limiter,
                          kb=MagicMock(), config=config)


@pytest.mark.asyncio
async def test_gate1_deflect_is_abstain_reason_gate1(monkeypatch):
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "ROUTER_V21", False)
    h = _make_handler(ollama=AsyncMock())
    # a personal-status query fires Gate-1; no structured answer → canned deflect
    monkeypatch.setattr(h, "_try_structured", AsyncMock(return_value=None))
    r = await h.handle(MessageRequest(user_id="u1", text="has my I-20 been approved yet", platform="telegram"))
    assert r.text == _KB_MISS_RESPONSE
    assert r.is_abstain is True and r.abstain_reason == "gate1"


@pytest.mark.asyncio
async def test_no_chunks_is_abstain_reason_kb_miss(monkeypatch):
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "ROUTER_V21", False)
    h = _make_handler(ollama=AsyncMock(), chunks=[])
    r = await h.handle(MessageRequest(user_id="u2", text="what is the airspeed of a swallow", platform="telegram"))
    assert r.text == _KB_MISS_RESPONSE
    assert r.is_abstain is True and r.abstain_reason == "kb-miss"


@pytest.mark.asyncio
async def test_pipeline_error_is_abstain_reason_error(monkeypatch):
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "ROUTER_V21", False)
    h = _make_handler(ollama=AsyncMock(), retrieve_raises=True)
    r = await h.handle(MessageRequest(user_id="u3", text="tell me about the GSA", platform="telegram"))
    assert r.text == _RAG_ERROR_RESPONSE
    assert r.is_abstain is True and r.abstain_reason == "error"


@pytest.mark.asyncio
async def test_normal_answer_is_not_abstain(monkeypatch):
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "ROUTER_V21", False)
    ollama = AsyncMock()
    ollama.generate_answer = AsyncMock(return_value="The GSA travel award is $500 for domestic conferences.")
    h = _make_handler(ollama=ollama)
    r = await h.handle(MessageRequest(user_id="u4", text="how much is the travel award", platform="telegram"))
    assert r.is_abstain is False and r.abstain_reason is None

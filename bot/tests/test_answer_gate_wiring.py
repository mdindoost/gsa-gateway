"""TDD tests for the answer-gate production wiring (ANSWER_GATE_ENABLED flag).

WS4 moved the over-answer guard to a POST-generation faithfulness/answerability gate: compose FIRST,
then decide answer|abstain via deterministic answer-type grounding + a subjective guard + robust quote
grounding, with a Gate-2 answerability verdict only for the non-typed factual residual.

  1. flag OFF (default) → personal-status question goes through OLD path (retriever called, no gate).
  2. flag ON + Gate-1 fires ("has my I-20 been approved") → canned deflection, retriever NOT called.
  3. flag ON + non-typed Gate-2 NOT_IN_CONTEXT → abstain; generate_answer WAS called (compose-first).
  4. flag ON + exemption: INTENT_SOCIAL bypasses the gate (ollama.generate NOT called for gate).
  5. flag ON + typed-grounded (money value present + grounded) → gate keeps it; Gate-2 LLM NOT called.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.config as botcfg
from bot.core.message_handler import MessageHandler, MessageRequest, _KB_MISS_RESPONSE
from bot.services.intent_detector import (
    INTENT_QUESTION,
    INTENT_SOCIAL,
)


# ─────────────────────────────────────────── shared helpers ──────────────────

def _make_chunk(text="The GSA travel award is $500 for domestic conferences."):
    c = MagicMock()
    c.text = text
    c.source_file = "gsa_faq.md"
    c.section_title = "Travel Award"
    c.relevance_score = 0.8
    return c


def _make_handler(ollama=None, *, top_relevance=0.9):
    rate_limiter = MagicMock()
    rate_limiter.is_allowed.return_value = True

    intent_detector = MagicMock()
    intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)

    conversation_manager = MagicMock()
    conversation_manager.get_session.return_value = None
    conversation_manager.get_history.return_value = []
    conversation_manager.get_mode.return_value = "gsa"

    config = MagicMock()
    config.conversation_max_turns = 5

    retriever = AsyncMock()
    retriever.top_relevance = MagicMock(return_value=top_relevance)
    retriever.retrieve = AsyncMock(return_value=[_make_chunk()])
    retriever.corpus_ready = MagicMock(return_value=False)  # disable deep-fallback

    return MessageHandler(
        retriever=retriever,
        ollama=ollama,
        conversation_manager=conversation_manager,
        intent_detector=intent_detector,
        db=MagicMock(),
        rate_limiter=rate_limiter,
        kb=MagicMock(),
        config=config,
    )


# ─────────────────────────────────────────── Case 1 ─────────────────────────
@pytest.mark.asyncio
async def test_case1_gate_off_personal_question_reaches_retriever(monkeypatch):
    """Flag OFF (default) → 'what is my financial aid balance' goes through the old path.
    Retriever must be called (gate is not wired)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate_answer = AsyncMock(return_value="Here is your balance information.")

    h = _make_handler(ollama=ollama)
    req = MessageRequest(user_id="u1", text="what is my financial aid balance", platform="discord")
    resp = await h.handle(req)

    # Gate was OFF → retriever should have been invoked on the normal path
    h.retriever.retrieve.assert_awaited()
    # Response should NOT be the gate deflection (it goes to normal generation)
    assert resp.text != _KB_MISS_RESPONSE


# ─────────────────────────────────────────── Case 2 ─────────────────────────
@pytest.mark.asyncio
async def test_case2_gate_on_gate1_fires_returns_deflection_no_retriever(monkeypatch):
    """Flag ON + Gate-1 fires on personal-record query → canned deflection returned; retriever
    NOT called (Gate-1 is pre-retrieval)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)

    h = _make_handler(ollama=ollama)
    req = MessageRequest(user_id="u1", text="has my I-20 been approved", platform="discord")
    resp = await h.handle(req)

    assert resp.text == _KB_MISS_RESPONSE
    # Retriever must NOT have been called — gate fired before retrieval
    h.retriever.retrieve.assert_not_awaited()


# ─────────────────────────────────────────── Case 3 ─────────────────────────
@pytest.mark.asyncio
async def test_case3_gate_on_nontyped_not_in_context_abstains_after_compose(monkeypatch):
    """Flag ON + non-typed factual question whose Gate-2 answerability verdict is NOT_IN_CONTEXT →
    the gate abstains (canned deflection). Because the gate is POST-generation, generate_answer WAS
    called (compose-first) — the deflection replaces the composed answer."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    gate2_json = '{"label": "NOT_IN_CONTEXT", "supporting_quote": "", "missing_piece": "not found"}'

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=gate2_json)                       # the Gate-2 verdict
    ollama.generate_answer = AsyncMock(return_value="A composed but unsupported answer.")

    h = _make_handler(ollama=ollama, top_relevance=0.3)
    req = MessageRequest(
        user_id="u1",
        text="what courses are required for the certificate program in data science",
        platform="discord",
    )
    resp = await h.handle(req)

    # gate abstained → honest deflection (Phase-4 useful-abstain: contact block present, not the answer)
    assert "gsa-pres@njit.edu" in resp.text
    assert "A composed but unsupported answer." not in resp.text
    ollama.generate_answer.assert_awaited()   # compose-first: the answer was composed, then gated
    ollama.generate.assert_awaited()          # the non-typed residual ran the Gate-2 answerability check


# ─────────────────────────────────────────── Case 4 ─────────────────────────
@pytest.mark.asyncio
async def test_case4_gate_on_intent_social_bypasses_gate2(monkeypatch):
    """Flag ON + INTENT_SOCIAL → Gate-2 is exempted; ollama.generate NOT called for gate."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=None)   # should NOT be called for gate
    ollama.generate_answer = AsyncMock(return_value="Here are some upcoming social events.")

    h = _make_handler(ollama=ollama, top_relevance=0.2)   # low ce — would trigger G2 if not exempt
    h.intent_detector.detect.return_value = (INTENT_SOCIAL, 0.9)

    req = MessageRequest(user_id="u1", text="any social events this week", platform="discord")
    resp = await h.handle(req)

    # Gate-2 LLM (ollama.generate) must NOT have been called for the gate check
    # (it may have been called for other ollama operations, but we verify generate_answer ran)
    ollama.generate.assert_not_awaited()
    ollama.generate_answer.assert_awaited_once()
    assert resp.text == "Here are some upcoming social events."


# ─────────────────────────────────────────── Case 5 ─────────────────────────
@pytest.mark.asyncio
async def test_case5_typed_grounded_answer_keeps_without_gate2(monkeypatch):
    """Flag ON + a typed ('how much') question whose composed answer carries a GROUNDED money value
    → the deterministic answer-type grounding keeps the answer WITHOUT any Gate-2 LLM call."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=None)   # deterministic path → gate2 NOT called
    # composed answer states $500, which is grounded in the default chunk text
    ollama.generate_answer = AsyncMock(return_value="The GSA travel award is $500 for domestic travel.")

    h = _make_handler(ollama=ollama, top_relevance=0.9)
    req = MessageRequest(
        user_id="u1",
        text="how much is the gsa travel award",
        platform="discord",
    )
    resp = await h.handle(req)

    # answer-type grounding (money) kept it deterministically → no Gate-2 LLM call
    ollama.generate.assert_not_awaited()
    ollama.generate_answer.assert_awaited_once()
    assert resp.text == "The GSA travel award is $500 for domestic travel."


# ─────────────────────────────────────────── Fix A ──────────────────────────
@pytest.mark.asyncio
async def test_gate1_structured_exempt_not_deflected(monkeypatch):
    """Fix A: Gate-1 fires on a personal phrasing but _try_structured returns a non-None KG
    answer → Gate-1 must NOT deflect; the structured path is allowed to answer.
    Regression: the old code deflected unconditionally on any Gate-1 cue."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)

    h = _make_handler(ollama=ollama)
    # Simulate a structured KG answer being available — Gate-1 must not suppress it
    h._try_structured = AsyncMock(return_value="The University Registrar is Jane Doe.")

    req = MessageRequest(user_id="u1", text="has my I-20 been approved", platform="discord")
    resp = await h.handle(req)

    # _try_structured was consulted and returned non-None → Gate-1 exempt, no deflect
    h._try_structured.assert_awaited()
    assert resp.text != _KB_MISS_RESPONSE
    assert resp.text == "The University Registrar is Jane Doe."


# ─────────────────────────────────────────── Fix B-1 ────────────────────────
@pytest.mark.asyncio
async def test_gate2_not_in_context_routes_to_live(monkeypatch):
    """Fix B: Gate-2 returns NOT_IN_CONTEXT and live has not run yet → handler tries live
    FIRST; a live hit prevents the canned deflection (answer is the live text, not the miss
    response).
    top_relevance=0.3: above LIVE_THRESHOLD (0.15) so primary_miss=False (live not auto-fired
    on primary miss)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "x")

    gate2_json = '{"label": "NOT_IN_CONTEXT", "supporting_quote": "", "missing_piece": "not found"}'

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=gate2_json)
    ollama.generate_answer = AsyncMock(return_value="A composed but unsupported answer.")

    h = _make_handler(ollama=ollama, top_relevance=0.3)

    live_result = MagicMock()
    live_result.text = "NJIT registration info from live search."
    live_result.source_url = "https://www.njit.edu/registrar"
    h.live_search = AsyncMock(return_value=live_result)

    req = MessageRequest(
        user_id="u1",
        text="what courses are required for the certificate program in data science",
        platform="discord",
    )
    resp = await h.handle(req)

    # gate abstained (NOT_IN_CONTEXT) → live was tried before deflecting; live hit → live text served
    h.live_search.assert_awaited_once()
    assert resp.text == "NJIT registration info from live search."
    assert resp.text != _KB_MISS_RESPONSE
    ollama.generate_answer.assert_awaited()   # compose-first even though the gate later abstained


# ─────────────────────────────────────────── Fix B-2 ────────────────────────
@pytest.mark.asyncio
async def test_gate2_not_in_context_no_live_deflects(monkeypatch):
    """Fix B (no-live variant): the gate abstains (NOT_IN_CONTEXT) and live is disabled →
    canned deflection is returned (no live tier available to rescue the answer)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    gate2_json = '{"label": "NOT_IN_CONTEXT", "supporting_quote": "", "missing_piece": "not found"}'

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=gate2_json)
    ollama.generate_answer = AsyncMock(return_value="A composed but unsupported answer.")

    h = _make_handler(ollama=ollama, top_relevance=0.3)

    req = MessageRequest(
        user_id="u1",
        text="what courses are required for the certificate program in data science",
        platform="discord",
    )
    resp = await h.handle(req)

    # gate abstained, live disabled → honest deflection (compose ran first, then was replaced)
    assert "gsa-pres@njit.edu" in resp.text
    assert "A composed but unsupported answer." not in resp.text
    ollama.generate_answer.assert_awaited()

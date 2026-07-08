"""TDD — Accuracy Quick-Wins Wave 2, QW-A8: structured/KG answers must log a question_id so the
connectors attach the 👍/👎/🔄 feedback keyboard and the tier becomes measurable.

DESIGN NOTE (deviation from the spec's `(text, skill)` return-type change — lower blast radius):
changing `_try_structured`'s return type breaks 3+ existing tests that mock it as a string. Instead:
  - The LIVE primary path `_answer_decision` (ROUTER_V21=1) already has `decision.skill` in scope →
    logs `matched_topic="kg:{skill}"` (granular) with NO return-type change.
  - The rarely-hit legacy `_try_structured` path (reached only when the v2.1 router returns None/COMMAND)
    logs a coarse `matched_topic="kg"`.
Both attach a question_id → both get the feedback keyboard. To be confirmed by Fable at diff review.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.config as botcfg
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


def _make_handler(ollama=None, *, unified_router=None):
    rate_limiter = MagicMock(); rate_limiter.is_allowed.return_value = True
    intent_detector = MagicMock(); intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    cm = MagicMock()
    cm.get_session.return_value = None
    cm.get_history.return_value = []
    cm.get_mode.return_value = "gsa"
    cm.get_pending.return_value = None
    config = MagicMock(); config.conversation_max_turns = 5
    db = MagicMock(); db.log_question.return_value = 4242            # the assigned question_id
    retriever = AsyncMock()
    retriever.top_relevance = MagicMock(return_value=0.9)
    retriever.retrieve = AsyncMock(return_value=[])
    retriever.corpus_ready = MagicMock(return_value=False)
    return MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=cm,
        intent_detector=intent_detector, db=db, rate_limiter=rate_limiter,
        kb=MagicMock(), config=config, unified_router=unified_router,
    )


# ═══════════════════════════ legacy _try_structured path (coarse "kg") ═══════════════════════════
@pytest.mark.asyncio
async def test_a8_legacy_structured_logs_and_attaches_question_id(monkeypatch):
    """When the legacy structured path answers (no v2.1 router), the response carries a question_id and
    a `matched_topic="kg"` analytics row — previously it returned a bare MessageResponse (no buttons)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)
    monkeypatch.setattr(botcfg, "FOLLOWUP_RESUME_ENABLED", False)   # isolate: no pending-register side effects

    h = _make_handler(ollama=AsyncMock())                 # unified_router=None → legacy path
    h._try_structured = AsyncMock(return_value="The University Registrar is Jane Doe.")

    req = MessageRequest(user_id="u1", text="who is the university registrar", platform="discord")
    resp = await h.handle(req)

    assert resp.text == "The University Registrar is Jane Doe."   # text unchanged
    assert resp.question_id == 4242                               # NEW: id attached → buttons render
    h.db.log_question.assert_called_once()
    assert h.db.log_question.call_args.kwargs["matched_topic"] == "kg"


# ═══════════════════════════ v2.1 _answer_decision KG path (granular "kg:{skill}") ══════════════
@pytest.mark.asyncio
async def test_a8_answer_decision_kg_logs_skill_and_question_id(monkeypatch):
    """The LIVE primary path logs `matched_topic="kg:{skill}"` (skill in scope) + attaches question_id."""
    monkeypatch.setattr(botcfg, "FOLLOWUP_RESUME_ENABLED", False)

    h = _make_handler(ollama=AsyncMock())
    # skip real SQL/compose — exercise only the logging + response wiring
    h._structured_from_route = MagicMock(return_value=("Facts: 30 faculty …", None, False, [], None))
    h._compose_structured = AsyncMock(return_value="The department has 30 faculty.")

    decision = MagicMock(family="KG", skill="faculty_in_department", args={"org_id": 7}, source="kg")
    req = MessageRequest(user_id="u1", text="how many faculty in CS", platform="telegram", guild_id=99)
    resp = await h._answer_decision(req, decision)

    assert resp.text == "The department has 30 faculty."
    assert resp.question_id == 4242
    h.db.log_question.assert_called_once()
    kw = h.db.log_question.call_args.kwargs
    assert kw["matched_topic"] == "kg:faculty_in_department"
    assert kw["platform"] == "telegram" and kw["guild_id"] == 99


@pytest.mark.asyncio
async def test_a8_answer_decision_empty_result_degrades_to_rag_no_log(monkeypatch):
    """An EMPTY structured result still degrades to RAG (honest-partial) and does NOT log a KG row."""
    monkeypatch.setattr(botcfg, "FOLLOWUP_RESUME_ENABLED", False)
    h = _make_handler(ollama=AsyncMock())
    h._structured_from_route = MagicMock(return_value=None)       # empty → RAG
    h._rag_pipeline = AsyncMock(return_value=MagicMock(text="rag answer", question_id=7))

    decision = MagicMock(family="KG", skill="faculty_in_department", args={}, source="kg")
    req = MessageRequest(user_id="u1", text="how many faculty in CS", platform="discord")
    resp = await h._answer_decision(req, decision)

    h._rag_pipeline.assert_awaited()                              # degraded to RAG
    # no KG log row for a degraded (empty) structured result — _answer_decision logs nothing itself;
    # the RAG pipeline (mocked here) owns its own logging (Fable note #1: assert_not_called is exact).
    h.db.log_question.assert_not_called()


# ═══════════════════════════ A12 interaction pin (documented, accepted) ═════════════════════════
@pytest.mark.asyncio
async def test_a8_kg_retry_runs_rag_pins_a12(monkeypatch):
    """PIN (A12): a KG answer now has a 🔄 button, and 🔄 re-runs pure RAG (temp 0.7), NOT the router.
    Accepted per buttons-on-every-answer; this test records the behavior so a future A12 fix is a
    deliberate change, not an accident."""
    h = _make_handler(ollama=AsyncMock())
    h._rag_pipeline = AsyncMock(return_value=MagicMock(text="retried via rag"))
    req = MessageRequest(user_id="u1", text="who is the registrar", platform="discord")
    await h.retry_question(req)
    h._rag_pipeline.assert_awaited_once()
    assert h._rag_pipeline.call_args.kwargs.get("temperature") == 0.7

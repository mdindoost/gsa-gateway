"""A15b Commit 3 — the person-scope compose guard in _rag_pipeline.

On a person-listing query: keep only chunks with an NJIT-Person entity_id (drop seminar/external
pollution); if ZERO stamped chunks, never compose from pollution — degrade to live, then abstain.
Flag-gated (PERSON_SCOPE_GUARD_ENABLED); fail-open; non-person queries untouched.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.config as botcfg
from bot.core.live_fallback import LiveAnswer
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


def _chunk(text, *, entity_id=None, rel=0.5, title="T"):
    return SimpleNamespace(
        item_id=1, source_file="njit", section_title=title, source_url=None,
        relevance_score=rel, text=text, metadata={"entity_id": entity_id, "ce_score": rel},
    )


@pytest.fixture
def handler():
    cm = MagicMock()
    cm.get_mode.return_value = "gsa"; cm.get_history.return_value = []; cm.get_session.return_value = None
    retriever = AsyncMock()
    retriever.top_relevance = MagicMock(return_value=0.5)   # above floor → no primary_miss
    ollama = AsyncMock()
    ollama.generate_answer.return_value = "Prof. X studies the brain."
    config = MagicMock(); config.conversation_max_turns = 5
    rl = MagicMock(); rl.is_allowed.return_value = True
    return MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=cm,
        intent_detector=MagicMock(), db=MagicMock(), rate_limiter=rl, kb=MagicMock(), config=config)


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setattr(botcfg, "PERSON_SCOPE_GUARD_ENABLED", True)
    monkeypatch.setattr(botcfg, "RETRIEVAL_DEEP_FALLBACK", False)
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", False)


async def _ask(handler, q="which professors study the brain"):
    return await handler._rag_pipeline(
        MessageRequest(user_id="u", text=q, platform="discord"), q, INTENT_QUESTION)


@pytest.mark.asyncio
async def test_guard_trims_to_stamped_person_chunks(handler, guard_on):
    stamped = _chunk("Elisa Kallioniemi studies brain stim.", entity_id="people.njit.edu/profile/eak42")
    pollution = _chunk("McGill visitor seminar on brain modeling.", entity_id=None)
    handler.retriever.retrieve.return_value = [stamped, pollution]
    await _ask(handler)
    # compose saw ONLY the stamped chunk
    passed = handler.ollama.generate_answer.await_args.kwargs["chunks"]
    assert passed == [stamped]


@pytest.mark.asyncio
async def test_guard_zero_stamped_degrades_to_abstain(handler, guard_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)   # no live → straight to abstain
    handler.retriever.retrieve.return_value = [
        _chunk("Seminar by an external speaker.", entity_id=None),
        _chunk("Another seminar pdf.", entity_id=None)]
    resp = await _ask(handler)
    handler.ollama.generate_answer.assert_not_awaited()   # never composed from pollution
    assert resp.is_abstain is True and resp.abstain_reason == "person-scope-abstain"


@pytest.mark.asyncio
async def test_guard_zero_stamped_degrades_to_live(handler, guard_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "k")
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", False)
    handler.live_search = AsyncMock(
        return_value=LiveAnswer(text="Real NJIT roster.", source_url="https://biology.njit.edu"))
    handler.retriever.retrieve.return_value = [_chunk("Seminar pdf.", entity_id=None)]
    resp = await _ask(handler)
    handler.live_search.assert_awaited()
    assert resp.is_live is True and "Real NJIT roster" in resp.text
    handler.ollama.generate_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_off_no_trim(handler, monkeypatch):
    monkeypatch.setattr(botcfg, "PERSON_SCOPE_GUARD_ENABLED", False)
    monkeypatch.setattr(botcfg, "RETRIEVAL_DEEP_FALLBACK", False)
    stamped = _chunk("stamped", entity_id="people.njit.edu/profile/x")
    pollution = _chunk("pollution", entity_id=None)
    handler.retriever.retrieve.return_value = [stamped, pollution]
    await _ask(handler)
    passed = handler.ollama.generate_answer.await_args.kwargs["chunks"]
    assert passed == [stamped, pollution]        # untouched


@pytest.mark.asyncio
async def test_non_person_query_untouched(handler, guard_on):
    # a policy question mentioning nothing person-listing → guard doesn't fire
    stamped = _chunk("stamped", entity_id="people.njit.edu/profile/x")
    pollution = _chunk("pollution prose", entity_id=None)
    handler.retriever.retrieve.return_value = [stamped, pollution]
    await _ask(handler, q="how do I apply for financial aid")
    passed = handler.ollama.generate_answer.await_args.kwargs["chunks"]
    assert passed == [stamped, pollution]        # not a person-seeking query → no trim

"""A1 Wave 2 — handler wiring for the gated live tier (opt-in + off-target degrade).

Covers the three seams the flags touch in _rag_pipeline:
  • LIVE_OPTIN suppresses the KB-miss AUTO-fire — we deflect + OFFER instead of searching.
  • Under opt-in, a non-Telegram deflection gets the cross-platform "search njit for X" hint;
    Telegram does NOT (it renders offer_live_search as a button — no double-offer).
  • An off-target live result (LiveLinks) renders as the honest top-3-links deflection
    (is_abstain, abstain_reason="live-offtarget"), on both the auto-fire and gate-rescue paths.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.config as botcfg
from bot.core.live_fallback import LiveAnswer, LiveLinks
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.services.intent_detector import INTENT_QUESTION


def _chunk(text="The library has study spaces.", rel=0.5):
    return SimpleNamespace(
        item_id=1, source_file="gsa.md", section_title="Library",
        relevance_score=rel, text=text,
    )


@pytest.fixture
def handler():
    cm = MagicMock()
    cm.get_mode.return_value = "gsa"
    cm.get_history.return_value = []
    cm.get_session.return_value = None
    retriever = AsyncMock()
    retriever.retrieve.return_value = [_chunk()]
    retriever.top_relevance = MagicMock(return_value=0.5)
    ollama = AsyncMock()
    config = MagicMock(); config.conversation_max_turns = 5
    rl = MagicMock(); rl.is_allowed.return_value = True
    return MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=cm,
        intent_detector=MagicMock(), db=MagicMock(), rate_limiter=rl,
        kb=MagicMock(), config=config,
    )


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    # Neutralize the deep-fallback rescue: it's on in .env, and the mocked retrieve_deep would
    # otherwise return a truthy AsyncMock that masquerades as rescued chunks on a KB miss.
    monkeypatch.setattr(botcfg, "RETRIEVAL_DEEP_FALLBACK", False)


async def _ask(handler, q="what are the current library hours please", platform="telegram"):
    return await handler._rag_pipeline(
        MessageRequest(user_id="u1", text=q, platform=platform), q, INTENT_QUESTION)


# ── opt-in suppresses the KB-miss auto-fire ────────────────────────────────────

@pytest.mark.asyncio
async def test_optin_suppresses_autofire_on_kb_miss(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", True)
    handler.retriever.retrieve.return_value = []          # KB miss
    handler.live_search = AsyncMock()                     # must NOT be called
    resp = await _ask(handler)
    handler.live_search.assert_not_awaited()
    assert resp.is_abstain is True
    assert resp.offer_live_search is True                 # OFFER instead of auto-search


@pytest.mark.asyncio
async def test_optin_off_autofires_on_kb_miss(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", False)
    handler.retriever.retrieve.return_value = []
    handler.live_search = AsyncMock(
        return_value=LiveAnswer(text="Open 24h.", source_url="https://library.njit.edu"))
    resp = await _ask(handler)
    handler.live_search.assert_awaited()
    assert resp.is_live is True


# ── cross-platform hint (N2) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_optin_hint_appended_on_discord(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", True)
    handler.ollama.generate_answer.return_value = (
        "The library has study spaces. For current hours, see library.njit.edu.")
    resp = await _ask(handler, platform="discord")
    assert resp.offer_live_search is True
    assert "search njit for" in resp.text.lower()


@pytest.mark.asyncio
async def test_optin_no_hint_on_telegram(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", True)
    handler.ollama.generate_answer.return_value = (
        "The library has study spaces. For current hours, see library.njit.edu.")
    resp = await _ask(handler, platform="telegram")
    assert resp.offer_live_search is True                 # button carries the offer
    assert "search njit for" not in resp.text.lower()     # no text hint (no double-offer)


@pytest.mark.asyncio
async def test_no_hint_when_optin_off(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", False)
    handler.ollama.generate_answer.return_value = (
        "The library has study spaces. For current hours, see library.njit.edu.")
    resp = await _ask(handler, platform="discord")
    assert "search njit for" not in resp.text.lower()


# ── off-target live degrade → top-3-links deflection ───────────────────────────

@pytest.mark.asyncio
async def test_offtarget_autofire_renders_links(handler, live_on, monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", False)
    handler.retriever.retrieve.return_value = []          # KB miss → auto-fire
    urls = ["https://njit.edu/a", "https://njit.edu/b", "https://njit.edu/c"]
    handler.live_search = AsyncMock(return_value=LiveLinks(urls=urls))
    resp = await _ask(handler)
    assert resp.is_abstain is True
    assert resp.abstain_reason == "live-offtarget"
    assert resp.is_live is False
    for u in urls:
        assert u in resp.text


@pytest.mark.asyncio
async def test_relevance_ok_keeps_on_parse_fail(handler):
    # Fable R1: Gate-2 emits unparseable JSON (parse_gate2 → parsed=False). The live spans are
    # verbatim njit.edu text, so a judge malfunction must KEEP (answer-bias), never drop.
    handler.ollama.generate = AsyncMock(return_value="this is not json")
    handler.ollama.num_ctx = 8192
    ok = await handler._live_relevance_ok("q", ["A verbatim span from an njit.edu page."])
    assert ok is True


@pytest.mark.asyncio
async def test_relevance_ok_drops_on_confident_not_in_context(handler):
    # The DROP only fires on a CONFIDENT not-answered (parsed NOT_IN_CONTEXT) — the qual-exam case.
    handler.ollama.generate = AsyncMock(
        return_value='{"supporting_quote": "", "label": "NOT_IN_CONTEXT", "missing_piece": "timing"}')
    handler.ollama.num_ctx = 8192
    ok = await handler._live_relevance_ok("q", ["Program overview prose, no qual-exam timing."])
    assert ok is False


@pytest.mark.asyncio
async def test_gate_on_weak_chunks_offtarget_still_composes(handler, live_on, monkeypatch):
    # Fable R2: LIVE_RELEVANCE_GATE on + LIVE_OPTIN off (the recommended FIRST flip state). A
    # weak-chunk primary-miss auto-fires live; live off-target → LiveLinks. WITH chunks present the
    # LiveLinks must NOT pre-empt compose+gate — a good weak-chunk answer can still be served, exactly
    # as today's live→None fallthrough. Turning on the "pure safety" gate must not newly deflect.
    monkeypatch.setattr(botcfg, "LIVE_OPTIN", False)
    monkeypatch.setattr(botcfg, "LIVE_RELEVANCE_GATE", True)
    handler.retriever.retrieve.return_value = [_chunk(rel=0.05)]      # weak chunk present
    handler.retriever.top_relevance = MagicMock(return_value=0.05)    # below floor → primary_miss
    handler.live_search = AsyncMock(return_value=LiveLinks(urls=["https://njit.edu/x"]))
    handler.ollama.generate_answer.return_value = "The library is open weekdays 8AM to midnight."
    resp = await _ask(handler)
    handler.ollama.generate_answer.assert_awaited()      # compose ran — links did NOT pre-empt it
    assert "8AM to midnight" in resp.text
    assert resp.abstain_reason != "live-offtarget"

"""TDD — Accuracy Quick-Wins batch, Wave 1 (spec 2026-07-04-accuracy-quick-wins-design.md).

  QW-A2  Gate-2 transport failure (generate→None) must KEEP the composed answer (never-withhold),
         while a non-empty unparseable Gate-2 response (the France case) still abstains.
  QW-A16 malformed "[: Dept]" citation artifacts (incl. **bold**-wrapped) stripped; benign brackets kept.
  QW-A14 keyword-only chunks (similarity==0.0) render "[Match: keyword only]", not a fabricated "70%".
  QW-A10 canned help/deflection strings advertise no retired slash commands; help mentions /qrcode.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.config as botcfg
from bot.core.message_handler import (
    MessageHandler, MessageRequest, _KB_MISS_RESPONSE, _HELP_RESPONSE,
    _strip_doc_citations,
)
from bot.services.intent_detector import INTENT_QUESTION
from bot.services.ollama_client import OllamaClient


# ─────────────────────────────────────────── shared helpers (mirror test_answer_gate_wiring) ──
def _make_chunk(text="The GSA certificate program covers several topics."):
    c = MagicMock()
    c.text = text
    c.source_file = "gsa_faq.md"
    c.section_title = "Certificate"
    c.relevance_score = 0.8
    return c


def _make_handler(ollama=None, *, top_relevance=0.3):
    rate_limiter = MagicMock(); rate_limiter.is_allowed.return_value = True
    intent_detector = MagicMock(); intent_detector.detect.return_value = (INTENT_QUESTION, 0.9)
    cm = MagicMock()
    cm.get_session.return_value = None
    cm.get_history.return_value = []
    cm.get_mode.return_value = "gsa"
    cm.get_pending.return_value = None
    config = MagicMock(); config.conversation_max_turns = 5
    retriever = AsyncMock()
    retriever.top_relevance = MagicMock(return_value=top_relevance)
    retriever.retrieve = AsyncMock(return_value=[_make_chunk()])
    retriever.corpus_ready = MagicMock(return_value=False)
    return MessageHandler(
        retriever=retriever, ollama=ollama, conversation_manager=cm,
        intent_detector=intent_detector, db=MagicMock(), rate_limiter=rate_limiter,
        kb=MagicMock(), config=config,
    )


# ═══════════════════════════════════ QW-A2 ═══════════════════════════════════
@pytest.mark.asyncio
async def test_a2_gate2_transport_none_keeps_answer(monkeypatch):
    """generate() returns None during Gate-2 (transport failure OR empty model response) → the
    already-composed answer is KEPT, not abstained. Never-withhold: a checker OUTAGE must not
    discard a real answer (mirrors the gate-EXCEPTION keep path)."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)   # isolate: no live rescue

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value=None)                      # Gate-2 TRANSPORT FAILURE
    ollama.generate_answer = AsyncMock(return_value="The certificate needs 4 core courses.")

    h = _make_handler(ollama=ollama, top_relevance=0.3)
    req = MessageRequest(
        user_id="u1",
        text="what courses are required for the certificate program in data science",
        platform="discord")
    resp = await h.handle(req)

    ollama.generate.assert_awaited()                       # the Gate-2 residual DID run (and failed)
    assert resp.text == "The certificate needs 4 core courses."   # kept, not deflected
    assert "gsa-pres@njit.edu" not in resp.text            # NOT the useful-abstain deflection


@pytest.mark.asyncio
async def test_a2_gate2_nonempty_unparseable_still_abstains(monkeypatch):
    """Regression: a NON-EMPTY unparseable Gate-2 response (the France / out-of-domain-garbage case)
    is deterministic model garbage, not a transport fault → still abstains. Preserves the WS4
    both-directions design premise."""
    monkeypatch.setattr(botcfg, "ANSWER_GATE_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)

    ollama = AsyncMock()
    ollama.rewrite_with_context = AsyncMock(return_value=None)
    ollama.expand_query = AsyncMock(return_value=None)
    ollama.generate = AsyncMock(return_value="I think it is probably yes, sure.")   # non-JSON garbage
    ollama.generate_answer = AsyncMock(return_value="A composed but unsupported answer.")

    h = _make_handler(ollama=ollama, top_relevance=0.3)
    req = MessageRequest(
        user_id="u1",
        text="what courses are required for the certificate program in data science",
        platform="discord")
    resp = await h.handle(req)

    assert "gsa-pres@njit.edu" in resp.text                       # abstained
    assert "A composed but unsupported answer." not in resp.text


# ═══════════════════════════════════ QW-A16 ══════════════════════════════════
def test_a16_strips_bold_wrapped_malformed_citation():
    """The exact live artifact: 'According to document **[: Mathematical Sciences]**, Prof X …'.
    No stray '****' or dangling connector may survive."""
    out = _strip_doc_citations(
        "According to document **[: Mathematical Sciences]**, Prof Nadim studies neural dynamics.")
    assert "[" not in out and "]" not in out
    assert "*" not in out
    assert "document" not in out.lower()
    assert "Prof Nadim studies neural dynamics." in out


def test_a16_strips_bare_and_docid_brackets():
    assert "[" not in _strip_doc_citations("Text [doc_id 5: Computer Science] more.")
    assert "[" not in _strip_doc_citations("Text [: Biological Sciences] more.")


def test_a16_keeps_benign_brackets():
    """A bracket whose pre-colon content is neither empty nor a doc_id must be UNTOUCHED,
    including when adjacent to unrelated bold emphasis."""
    keep1 = "See **the guide** [Note: draft] for details."
    keep2 = "Office hours are [10:30] to noon; see [Source: https://njit.edu]."
    assert _strip_doc_citations(keep1) == keep1
    assert _strip_doc_citations(keep2) == keep2


def test_a16_wellformed_docid_still_stripped():
    out = _strip_doc_citations("According to doc_id 5 (Computer Science): the dean is X.")
    assert "doc_id" not in out
    assert "the dean is X." in out


# ═══════════════════════════════════ QW-A14 ══════════════════════════════════
def _ctx_chunk(*, similarity, relevance_score):
    c = MagicMock()
    c.source_file = "gsa_faq.md"; c.item_id = 12; c.section_title = "S"
    c.source_url = None; c.text = "body"; c.verified = True; c.metadata = {}
    c.similarity = similarity; c.relevance_score = relevance_score
    return c


def test_a14_keyword_only_hit_labeled_honestly():
    """A keyword-only hit reaches the prompt builder as similarity==0.0 (the shim coerces
    `c.similarity or 0.0`). It must render '[Match: keyword only]', never a fabricated percentage."""
    oc = OllamaClient.__new__(OllamaClient)            # no network init
    block = oc._build_context_block([_ctx_chunk(similarity=0.0, relevance_score=0.7)])
    assert "keyword only" in block
    assert "70%" not in block


def test_a14_vector_hit_shows_real_percent():
    oc = OllamaClient.__new__(OllamaClient)
    block = oc._build_context_block([_ctx_chunk(similarity=0.46, relevance_score=0.46)])
    assert "46%" in block
    assert "keyword only" not in block


# ═══════════════════════════════════ QW-A10 ══════════════════════════════════
_DEAD = ("/events", "/contact", "/resources")


def test_a10_kb_miss_response_has_no_dead_commands():
    for cmd in _DEAD:
        assert cmd not in _KB_MISS_RESPONSE


def test_a10_help_response_clean_and_mentions_qrcode():
    for cmd in _DEAD:
        assert cmd not in _HELP_RESPONSE
    assert "/qrcode" in _HELP_RESPONSE


def test_a10_useful_abstain_has_no_dead_commands():
    h = _make_handler(ollama=AsyncMock())
    txt = h._useful_abstain("some question", [])
    for cmd in _DEAD:
        assert cmd not in txt

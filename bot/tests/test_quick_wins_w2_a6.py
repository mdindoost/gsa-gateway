"""TDD — Accuracy Quick-Wins Wave 2, QW-A6: the faithfulness gate must judge against the SAME
context generation saw, and its Gate-2 LLM call must pass num_ctx.

Bug: the gate built passages from chunks[:5][:1200] while generation fit up to the model budget, so a
grounded typed value (count/rate/money/date) past char 1200 — common on the deep-fallback tier (whole
parent pages) — false-abstained.

Fix (Fable-shaped): (1) the DETERMINISTIC checks see the FULL fitted text (pure Python, no prompt risk);
(2) the Gate-2 LLM gets a BOUNDED window AND num_ctx passed (without num_ctx a long context front-
truncates the SYSTEM prompt → non-JSON → false-abstain, INVERTING the fix); (3) OllamaClient.prefit()
exposes the same fitted chunks generation used, so the caller shows the gate exactly what the model saw.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.config as botcfg
from bot.core.message_handler import MessageHandler
from bot.services.intent_detector import INTENT_QUESTION
from bot.services.ollama_client import OllamaClient


def _make_handler(ollama):
    rl = MagicMock(); rl.is_allowed.return_value = True
    idet = MagicMock(); idet.detect.return_value = (INTENT_QUESTION, 0.9)
    cm = MagicMock(); cm.get_mode.return_value = "gsa"; cm.get_history.return_value = []
    cfg = MagicMock(); cfg.conversation_max_turns = 5
    return MessageHandler(retriever=AsyncMock(), ollama=ollama, conversation_manager=cm,
                          intent_detector=idet, db=MagicMock(), rate_limiter=rl, kb=MagicMock(), config=cfg)


def _chunk(text):
    c = MagicMock(); c.text = text; c.source_file = "f.md"; c.item_id = 1
    c.section_title = "S"; c.source_url = None; c.verified = True; c.metadata = {}; c.similarity = 0.5
    return c


# ═══════════════ (1) deterministic checks see the FULL fitted text ═══════════════
@pytest.mark.asyncio
async def test_a6_typed_value_past_char_1200_is_kept():
    """A grounded money value sitting past char 1200 must be found → gate KEEPS (was false-abstain)."""
    h = _make_handler(AsyncMock())
    body = ("Background prose. " * 100) + "The late payment fee is $150 per semester."  # $150 ≈ char 1800
    assert len(body) > 1400
    keep, why = await h._faithfulness_gate("how much is the late payment fee",
                                           "The late payment fee is $150.", [_chunk(body)])
    assert keep is True


@pytest.mark.asyncio
async def test_a6_ungrounded_typed_value_still_abstains():
    """Regression: a typed answer whose value is NOWHERE in the passages still abstains."""
    h = _make_handler(AsyncMock())
    keep, why = await h._faithfulness_gate("how much is the late payment fee",
                                           "The late payment fee is $999.", [_chunk("Prose with no fee figure.")])
    assert keep is False


# ═══════════════ (2) Gate-2 LLM call passes num_ctx (inversion guard) ═══════════════
@pytest.mark.asyncio
async def test_a6_gate2_call_passes_num_ctx():
    ollama = AsyncMock(); ollama.num_ctx = 16384
    ollama.generate = AsyncMock(
        return_value='{"label":"NOT_IN_CONTEXT","supporting_quote":"","missing_piece":"x"}')
    h = _make_handler(ollama)
    # non-typed factual question → routes to the Gate-2 LLM call
    await h._faithfulness_gate("what is the policy on booking library rooms",
                               "Rooms can be booked online through the portal.", [_chunk("Library prose.")])
    ollama.generate.assert_awaited()
    opts = ollama.generate.call_args.kwargs["options"]
    assert opts.get("num_ctx") == 16384          # without this, Ollama front-truncates the system prompt
    assert opts.get("num_predict") == 256


@pytest.mark.asyncio
async def test_a6_gate2_transport_none_still_keeps():
    """A2 must still hold with the A6 changes: Gate-2 generate()->None keeps the answer."""
    ollama = AsyncMock(); ollama.num_ctx = 16384
    ollama.generate = AsyncMock(return_value=None)
    h = _make_handler(ollama)
    keep, why = await h._faithfulness_gate("what is the room booking policy",
                                           "Rooms are booked online.", [_chunk("Library prose.")])
    assert keep is True and why == "gate2-transport-keep"


# ═══════════════ (3) prefit() exposes the fitted set ═══════════════
def test_a6_prefit_returns_fitting_chunks():
    oc = OllamaClient.__new__(OllamaClient)      # no network init
    oc.num_ctx = 16384
    chunks = [_chunk("Short page one."), _chunk("Short page two.")]
    fitted = oc.prefit("a question", chunks, conversation_history=None)
    assert [c.text for c in fitted] == [c.text for c in chunks]   # both small pages fit → unchanged


def test_a6_prefit_empty_chunks():
    oc = OllamaClient.__new__(OllamaClient); oc.num_ctx = 16384
    assert oc.prefit("q", [], None) == []


# ═══════════════ (4) gate-prompt SIZE is bounded (regression guard against re-inversion) ═══════════════
@pytest.mark.asyncio
async def test_a6_gate2_prompt_size_bounded():
    """Spec-mandated guard: even with huge fitted chunks, the Gate-2 LLM prompt stays well inside
    num_ctx — so a future edit that feeds the FULL context to Gate-2 (re-introducing the inversion
    Fable identified) fails LOUDLY here instead of silently regressing every abstain."""
    ollama = AsyncMock(); ollama.num_ctx = 16384
    ollama.generate = AsyncMock(
        return_value='{"label":"NOT_IN_CONTEXT","supporting_quote":"","missing_piece":"x"}')
    h = _make_handler(ollama)
    big = [_chunk("x " * 10000) for _ in range(8)]      # 8 chunks ~20k chars each
    await h._faithfulness_gate("what is the room booking policy", "Rooms are booked online.", big)
    kw = ollama.generate.call_args.kwargs
    assert len(kw["prompt"]) + len(kw["system"]) < 8000   # bounded; system prompt can't be front-truncated

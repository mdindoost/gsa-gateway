"""Thread B — the v1 LLM query-expander (GSA-framing short-query rewriter) is removed ENTIRELY.

Fable's condition #1: delete the call site AND the definitions, so the GSA-framing prompt literally
does not exist in the codebase (a dead method is a latent re-introduction hazard). These assert the
symbols are gone; the behavioral guarantee (short query retrieved verbatim) lives in
test_message_handler.py::test_short_query_not_gsa_reframed.
"""
from __future__ import annotations

import bot.services.ollama_client as oc
from bot.services.ollama_client import OllamaClient


def test_expand_query_method_removed():
    assert not hasattr(OllamaClient, "expand_query"), \
        "expand_query must be deleted, not just uncalled — it GSA-frames every short query"


def test_expand_prompt_constants_removed():
    assert not hasattr(oc, "_EXPAND_SYSTEM"), "_EXPAND_SYSTEM (GSA-framing system prompt) must be gone"
    assert not hasattr(oc, "_EXPAND_EXAMPLES"), "_EXPAND_EXAMPLES (GSA-framing few-shot) must be gone"


def test_no_gsa_framing_prompt_text_left_in_module():
    # Belt-and-suspenders: the tell-tale instruction string must not survive anywhere in the module.
    import inspect
    src = inspect.getsource(oc)
    assert "clear, specific question about" not in src, "residual GSA-framing prompt text found"

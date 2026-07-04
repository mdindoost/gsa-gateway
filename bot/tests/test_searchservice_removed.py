"""Follow-up: the dead v1 SearchService module is removed (2026-07-03).

SearchService was instantiated in main.py but never called anywhere in live code — a fully inert v1
object (the chat path uses the v2 retriever). Its `_expand_query` helper was distinct from the v1 LLM
expander removed in thread B. Whole-module delete: search.py + its test + the main.py wiring + fixture.
"""
from __future__ import annotations

import pytest


def test_searchservice_module_removed():
    with pytest.raises(ImportError):
        import bot.services.search  # noqa: F401


def test_no_search_svc_wiring_in_main():
    import inspect
    import bot.main
    src = inspect.getsource(bot.main)
    assert "SearchService" not in src, "dead SearchService import/instantiation must be gone from main"
    assert "search_svc" not in src, "dead self.search_svc wiring must be gone from main"

"""TDD — A1 Wave 1: maybe_answer_live relevance-gate + top-3-links degrade + max_pages=3.

The live tier becomes gated: an off-target page (grounded verbatim spans that don't ANSWER the
question) is skipped; when no page answers, degrade to an honest top-3-links list instead of a
confident wrong extract. relevance_ok=None (flag off) = today's first-grounded-wins behavior.
"""
from types import SimpleNamespace

import pytest

import bot.core.live_fallback as lf
from bot.core.live_fallback import maybe_answer_live, LiveAnswer, LiveLinks


def _wire(monkeypatch, urls, *, grounds=None):
    """search→urls, fetch→ok html, generate→raw; ground_spans grounds every page to spans=[url]
    (so `relevance_ok` decides pass/fail by url), unless `grounds` maps a url→None (no grounding)."""
    grounds = grounds or {}
    search_fn = lambda q: list(urls)
    fetch_fn = lambda u: (u, f"<html>{u}</html>", "ok")

    async def generate(system, user):
        return "raw"

    def fake_ground(raw, page_text, url):
        if grounds.get(url) == "none":
            return None
        return SimpleNamespace(spans=[url], source_url=url)

    monkeypatch.setattr(lf, "ground_spans", fake_ground)
    monkeypatch.setattr(lf, "clean_text", lambda html: html)
    monkeypatch.setattr(lf, "build_extract_prompt", lambda q, t: ("sys", "usr"))
    return dict(search_fn=search_fn, fetch_fn=fetch_fn, generate=generate)


@pytest.mark.asyncio
async def test_offtarget_page_skipped_next_page_served(monkeypatch):
    w = _wire(monkeypatch, ["u1", "u2", "u3"])
    async def rel_ok(q, spans):        # only u2 answers
        return spans == ["u2"]
    res = await maybe_answer_live("q", **w, relevance_ok=rel_ok, degrade_links=True)
    assert isinstance(res, LiveAnswer) and res.source_url == "u2"


@pytest.mark.asyncio
async def test_all_offtarget_degrades_to_top3_links(monkeypatch):
    w = _wire(monkeypatch, ["u1", "u2", "u3", "u4"])
    async def rel_ok(q, spans):
        return False               # nothing answers
    res = await maybe_answer_live("q", **w, relevance_ok=rel_ok, degrade_links=True)
    assert isinstance(res, LiveLinks) and res.urls == ["u1", "u2", "u3"]   # top 3 only


@pytest.mark.asyncio
async def test_all_offtarget_no_degrade_returns_none(monkeypatch):
    # flag-off identity: without degrade_links, exhaustion → None (today's LIVE_NOT_FOUND path)
    w = _wire(monkeypatch, ["u1", "u2"])
    async def rel_ok(q, spans):
        return False
    res = await maybe_answer_live("q", **w, relevance_ok=rel_ok, degrade_links=False)
    assert res is None


@pytest.mark.asyncio
async def test_gate_off_first_grounded_wins(monkeypatch):
    # relevance_ok=None → unchanged: first grounded page is served
    w = _wire(monkeypatch, ["u1", "u2"])
    res = await maybe_answer_live("q", **w, relevance_ok=None, degrade_links=False)
    assert isinstance(res, LiveAnswer) and res.source_url == "u1"


@pytest.mark.asyncio
async def test_relevance_fault_keeps_answer(monkeypatch):
    # never-withhold: a faulting relevance gate must KEEP the answer, not crash/drop
    w = _wire(monkeypatch, ["u1"])
    async def rel_ok(q, spans):
        raise RuntimeError("boom")
    res = await maybe_answer_live("q", **w, relevance_ok=rel_ok, degrade_links=True)
    assert isinstance(res, LiveAnswer) and res.source_url == "u1"


@pytest.mark.asyncio
async def test_no_urls_returns_none_even_with_degrade(monkeypatch):
    w = _wire(monkeypatch, [])
    res = await maybe_answer_live("q", **w, relevance_ok=None, degrade_links=True)
    assert res is None


@pytest.mark.asyncio
async def test_max_pages_3_reaches_third(monkeypatch):
    # page 1 & 2 fail relevance, page 3 answers → served (the max_pages 2→3 recall gain)
    w = _wire(monkeypatch, ["u1", "u2", "u3"])
    async def rel_ok(q, spans):
        return spans == ["u3"]
    res = await maybe_answer_live("q", **w, relevance_ok=rel_ok, degrade_links=True)
    assert isinstance(res, LiveAnswer) and res.source_url == "u3"

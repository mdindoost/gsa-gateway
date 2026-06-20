import asyncio
from bot.core.live_fallback import maybe_answer_live

PAGE_HTML = "<html><body><p>Visitor parking is available in the Lock Street Deck.</p></body></html>"


def _run(coro):
    return asyncio.run(coro)


def _gen(payload):
    async def g(system, user):
        return payload
    return g


def test_returns_grounded_answer_with_link():
    search_fn = lambda q: ["https://www.njit.edu/parking"]
    fetch_fn = lambda u: ("https://www.njit.edu/parking", PAGE_HTML, "ok")
    gen = _gen('{"spans": ["Visitor parking is available in the Lock Street Deck."]}')
    ans = _run(maybe_answer_live("where do visitors park", search_fn=search_fn,
                                 fetch_fn=fetch_fn, generate=gen))
    assert ans is not None
    assert "Lock Street Deck" in ans.text
    assert ans.source_url == "https://www.njit.edu/parking"
    # framing makes the live/different-source nature unmistakable (no "just now" overclaim)
    assert "Live from NJIT's website" in ans.text
    assert "just now" not in ans.text.lower()
    # source is NOT embedded in the body — connectors render it once via source_note
    # (the field still carries it); avoids the double-source on Telegram + Discord.
    assert "Source:" not in ans.text


def test_none_when_no_search_results():
    ans = _run(maybe_answer_live("x", search_fn=lambda q: [], fetch_fn=lambda u: ("", "", "200"),
                                 generate=_gen('{"spans": []}')))
    assert ans is None


def test_none_when_page_does_not_answer():
    search_fn = lambda q: ["https://www.njit.edu/parking"]
    fetch_fn = lambda u: ("https://www.njit.edu/parking", PAGE_HTML, "ok")
    gen = _gen('{"spans": []}')  # page has no answer
    assert _run(maybe_answer_live("tuition cost", search_fn=search_fn, fetch_fn=fetch_fn,
                                  generate=gen)) is None


def test_skips_failed_fetch_then_tries_next():
    calls = {"n": 0}

    def fetch_fn(u):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("", "", "HTTP 404")   # first result fails
        return ("https://www.njit.edu/parking", PAGE_HTML, "ok")
    search_fn = lambda q: ["https://www.njit.edu/bad", "https://www.njit.edu/parking"]
    gen = _gen('{"spans": ["Visitor parking is available in the Lock Street Deck."]}')
    ans = _run(maybe_answer_live("parking", search_fn=search_fn, fetch_fn=fetch_fn, generate=gen))
    assert ans is not None and calls["n"] == 2

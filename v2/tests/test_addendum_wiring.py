import asyncio

from bot.core.message_handler import MessageHandler


def test_cap_platform():
    assert MessageHandler._cap("telegram") == 4096
    assert MessageHandler._cap("discord") == 2000
    assert MessageHandler._cap(None) == 2000


def test_compose_appends_addendum():
    h = MessageHandler.__new__(MessageHandler)
    h.ollama = None
    out = asyncio.run(h._compose_structured(
        "q", "CARD", "", True,
        addendum={"awards": "Awards & honors: X", "prose": None}, platform="discord"))
    assert out.startswith("CARD") and "Awards & honors: X" in out


def test_compose_no_addendum_unchanged():
    h = MessageHandler.__new__(MessageHandler)
    h.ollama = None
    out = asyncio.run(h._compose_structured("q", "CARD", "", True, addendum=None, platform="discord"))
    assert out == "CARD"


def test_compose_prose_stubbed_when_over_budget():
    h = MessageHandler.__new__(MessageHandler)
    h.ollama = None
    payload = {"awards": None, "prose": {"title": "Bio", "content": "Y" * 5000, "url": "http://s"}}
    out = asyncio.run(h._compose_structured("q", "CARD", "", True, addendum=payload, platform="discord"))
    assert "Y" * 5000 not in out and "http://s" in out       # never partial; pointer instead


def test_payload_helper_gated_off(monkeypatch):
    import bot.config as botcfg
    h = MessageHandler.__new__(MessageHandler)
    monkeypatch.setattr(botcfg, "PERSON_ADDENDUM_ENABLED", False)
    assert h._person_addendum_payload(None, "entity_card", {"entity_id": "k/x"}) is None

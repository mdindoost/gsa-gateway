import importlib


def test_router_v21_defaults_off(monkeypatch):
    monkeypatch.delenv("ROUTER_V21", raising=False)
    monkeypatch.delenv("ROUTER_V21_SHADOW", raising=False)
    monkeypatch.delenv("ROUTER_V21_SLOT_RECOVERY", raising=False)
    import bot.config as c
    importlib.reload(c)
    assert c.ROUTER_V21 is False
    assert c.ROUTER_V21_SHADOW is True
    assert c.ROUTER_V21_SLOT_RECOVERY is False


def test_router_v21_enabled_via_env(monkeypatch):
    monkeypatch.setenv("ROUTER_V21", "1")
    import bot.config as c
    importlib.reload(c)
    assert c.ROUTER_V21 is True
    monkeypatch.delenv("ROUTER_V21", raising=False)
    importlib.reload(c)

import importlib
import os
from v2.core.retrieval import query_correct


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    assert query_correct.enabled() is False


def test_flag_on(monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    assert query_correct.enabled() is True


def test_botcfg_exposes_flag(monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    import bot.config as botcfg
    importlib.reload(botcfg)
    assert botcfg.QUERY_CORRECT_ENABLED is True

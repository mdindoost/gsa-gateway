"""Tests for the unified mode model: Mode enum, ConversationModeStore, ModeRegistry."""
import threading

import pytest

from bot.core.modes import (
    ConversationModeStore,
    Mode,
    ModeRegistry,
)


# ── Mode enum ────────────────────────────────────────────────────────────────

def test_mode_values_match_legacy_strings():
    # Back-compat: persisted/logged values are plain strings; the enum must equal them.
    assert Mode.GSA == "gsa"
    assert Mode.FREE == "free"
    assert Mode.JUDGE == "judge"
    assert Mode.PRESENTER == "presenter"
    assert Mode.AUDIENCE == "audience"


def test_mode_value_is_the_plain_string():
    # log_question(mode=...) must persist "free", never "Mode.FREE".
    assert Mode.FREE.value == "free"
    assert str(Mode.FREE.value) == "free"


def test_is_judging_predicate():
    assert Mode.JUDGE.is_judging
    assert Mode.PRESENTER.is_judging
    assert Mode.AUDIENCE.is_judging
    assert not Mode.GSA.is_judging
    assert not Mode.FREE.is_judging


# ── ConversationModeStore ────────────────────────────────────────────────────

def test_store_defaults_to_gsa():
    store = ConversationModeStore()
    assert store.get("never_seen") == Mode.GSA


def test_store_set_and_get():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)
    assert store.get("u1") == Mode.FREE


def test_store_reset_returns_to_gsa():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)
    store.reset("u1")
    assert store.get("u1") == Mode.GSA


def test_store_isolates_users():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)
    assert store.get("u2") == Mode.GSA


def test_store_accepts_legacy_string_values():
    # Back-compat: existing call sites pass the bare string "free"/"gsa".
    store = ConversationModeStore()
    store.set("u1", "free")
    assert store.get("u1") == Mode.FREE


def test_store_is_thread_safe_under_concurrent_writes():
    store = ConversationModeStore()

    def worker(uid):
        for _ in range(500):
            store.set(uid, Mode.FREE)
            store.get(uid)
            store.set(uid, Mode.GSA)

    threads = [threading.Thread(target=worker, args=(f"u{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No exception/corruption; final state is deterministic per user.
    for i in range(8):
        assert store.get(f"u{i}") == Mode.GSA


# ── ModeRegistry: derive judging, fall back to conversation ──────────────────

class _FakeJudging:
    """Stand-in exposing mode_of()/is_trigger() like JudgingSessionManager."""
    def __init__(self):
        self._modes = {}
        self.triggers = set()

    def mode_of(self, user_id):
        return self._modes.get(user_id)

    def is_trigger(self, text):
        return text.strip().lower() in self.triggers


def test_registry_without_judging_returns_conversation_mode():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)
    reg = ModeRegistry(store, judging=None)
    assert reg.get("u1") == Mode.FREE
    assert reg.get("u2") == Mode.GSA


def test_registry_judging_takes_precedence_when_active():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)            # conversation says FREE
    judging = _FakeJudging()
    judging._modes["u1"] = Mode.JUDGE     # but user is mid-judging
    reg = ModeRegistry(store, judging=judging)
    assert reg.get("u1") == Mode.JUDGE


def test_registry_falls_back_when_judging_inactive():
    store = ConversationModeStore()
    store.set("u1", Mode.FREE)
    judging = _FakeJudging()              # mode_of returns None
    reg = ModeRegistry(store, judging=judging)
    assert reg.get("u1") == Mode.FREE


def test_registry_default_gsa_with_judging_inactive():
    reg = ModeRegistry(ConversationModeStore(), judging=_FakeJudging())
    assert reg.get("fresh") == Mode.GSA

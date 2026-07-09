from bot.services.ollama_client import BASE_SYSTEM_PROMPT
from v2.core.retrieval.faithfulness import is_explicit_nonanswer


def test_base_prompt_has_time_qualifier_guard():
    p = BASE_SYSTEM_PROMPT.lower()
    assert "time or schedule qualifier" in p
    assert "next semester" in p
    # must instruct NOT to assert an unconfirmed qualifier
    assert "do not assert" in p


def test_rule14_does_not_seed_a_self_decline_phrase():
    """Rule 14 must NOT instruct the model to append an 'I don't have …' / 'not in our data'
    self-note: the WS4 self-abstain regex (is_explicit_nonanswer) treats any such phrase as a
    FULL decline and the gate discards the whole (correct) honest-partial answer. Rule 14 keeps
    ONLY assert-suppression; the deterministic 'what's missing' note is deferred to
    Qualifier-Scope Component C. (Ships assert-suppression, DEFERS the gap-note — see
    docs/superpowers/specs/2026-07-09-qualifier-scope-design.md §4.)"""
    p = BASE_SYSTEM_PROMPT.lower()
    assert "say you don't have" not in p
    assert "not in our data" not in p
    # the anti-fabrication core is retained
    assert "do not assert that qualifier as fact" in p


def test_gate_kills_the_phrasing_rule14_used_to_seed():
    """Locks WHY the directive had to go: the exact self-note rule 14 used to seed is caught by
    the self-abstain regex, while the de-noted answer survives it."""
    assert is_explicit_nonanswer(
        "CS 634 is taught by Vincent Oria. I don't have next-semester scheduling."
    ) is True
    assert is_explicit_nonanswer("CS 634 is taught by Vincent Oria.") is False

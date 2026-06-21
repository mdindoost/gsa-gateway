from v2.core.retrieval.unified_router import UnifiedRouter, RouteDecision
from bot.services.intent_detector import IntentDetector


def _router():
    return UnifiedRouter(db_path=":memory:", classifier=None, intent_detector=IntentDetector())


def test_greeting_is_a_command():
    d = _router().command_layer("hi")
    assert d is not None and d.family == "COMMAND" and d.command_intent == "greeting"


def test_clear_is_a_command():
    d = _router().command_layer("clear")
    assert d is not None and d.command_intent == "clear_history"


def test_food_is_not_a_command():
    assert _router().command_layer("is there free pizza today") is None


def test_real_question_is_not_a_command():
    assert _router().command_layer("who teaches cs") is None

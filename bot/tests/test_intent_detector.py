"""Tests for the IntentDetector service."""

import pytest

from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FOOD,
    INTENT_FREE_MODE,
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_IDENTITY,
    INTENT_GSA_MODE,
    INTENT_QUESTION,
    INTENT_STATEMENT,
    INTENT_THANKS,
    IntentDetector,
)


@pytest.fixture
def detector() -> IntentDetector:
    return IntentDetector()


# ── Food intent ───────────────────────────────────────────────────────────────

def test_food_query_detected_explicit(detector):
    intent, conf = detector.detect("any free food today?")
    assert intent == INTENT_FOOD
    assert conf == 1.0


def test_food_query_hungry(detector):
    intent, _ = detector.detect("I'm hungry, is there any food?")
    assert intent == INTENT_FOOD


def test_food_query_snacks(detector):
    intent, _ = detector.detect("are there snacks at the event?")
    assert intent == INTENT_FOOD


def test_food_keyword_lunch(detector):
    intent, _ = detector.detect("when is the lunch event?")
    assert intent == INTENT_FOOD


# ── Greeting intent ───────────────────────────────────────────────────────────

def test_greeting_hi(detector):
    intent, conf = detector.detect("hi")
    assert intent == INTENT_GREETING


def test_greeting_hello(detector):
    intent, _ = detector.detect("hello there")
    assert intent == INTENT_GREETING


def test_greeting_hey(detector):
    intent, _ = detector.detect("hey!")
    assert intent == INTENT_GREETING


def test_greeting_good_morning(detector):
    intent, _ = detector.detect("good morning")
    assert intent == INTENT_GREETING


def test_long_greeting_not_detected(detector):
    # Should NOT be greeting because message is too long
    long_msg = "hi i have a really detailed question about the travel award process"
    intent, _ = detector.detect(long_msg)
    assert intent != INTENT_GREETING


# ── Thanks intent ─────────────────────────────────────────────────────────────

def test_thanks_detected(detector):
    intent, _ = detector.detect("thanks!")
    assert intent == INTENT_THANKS


def test_thank_you_detected(detector):
    intent, _ = detector.detect("thank you")
    assert intent == INTENT_THANKS


def test_that_helps_detected(detector):
    intent, _ = detector.detect("that helps")
    assert intent == INTENT_THANKS


def test_got_it_detected(detector):
    intent, _ = detector.detect("got it")
    assert intent == INTENT_THANKS


# ── Question intent ───────────────────────────────────────────────────────────

def test_question_with_mark(detector):
    intent, conf = detector.detect("what are the penalties for clubs?")
    assert intent == INTENT_QUESTION
    assert conf >= 0.9


def test_how_question(detector):
    intent, _ = detector.detect("how do I apply for a travel award?")
    assert intent == INTENT_QUESTION


def test_who_question(detector):
    intent, _ = detector.detect("who is the GSA president?")
    assert intent == INTENT_QUESTION


def test_can_question(detector):
    intent, _ = detector.detect("can I apply for funding?")
    assert intent == INTENT_QUESTION


# ── Clear history intent ──────────────────────────────────────────────────────

def test_clear_detected(detector):
    intent, conf = detector.detect("clear")
    assert intent == INTENT_CLEAR_HISTORY
    assert conf == 1.0


def test_start_over_detected(detector):
    intent, _ = detector.detect("start over")
    assert intent == INTENT_CLEAR_HISTORY


def test_reset_detected(detector):
    intent, _ = detector.detect("reset")
    assert intent == INTENT_CLEAR_HISTORY


def test_forget_detected(detector):
    intent, _ = detector.detect("forget everything")
    assert intent == INTENT_CLEAR_HISTORY


@pytest.mark.parametrize("msg", [
    "how do I reset my NJIT password",
    "how do I clear my schedule",
    "did you forget my question",
    "I want to start over my application",
    "what is the travel award reset policy",
])
def test_clear_keyword_in_question_does_not_wipe_history(detector, msg):
    """Regression: a question that merely contains clear/reset/forget must NOT trigger a
    conversation wipe — only a standalone clear command should (eval #77)."""
    intent, _ = detector.detect(msg)
    assert intent != INTENT_CLEAR_HISTORY


# ── Help intent ───────────────────────────────────────────────────────────────

def test_help_command(detector):
    intent, _ = detector.detect("help")
    assert intent == INTENT_HELP


def test_what_can_you_do(detector):
    intent, _ = detector.detect("what can you do")
    assert intent == INTENT_HELP


# ── should_respond ────────────────────────────────────────────────────────────

def test_should_respond_in_ask_gsa_channel(detector):
    result = detector.should_respond(
        message="what is GSA?",
        channel_name="ask-gsa",
        bot_was_mentioned=False,
        ask_gsa_channel="ask-gsa",
    )
    assert result is True


def test_should_respond_to_mention(detector):
    result = detector.should_respond(
        message="what is GSA?",
        channel_name="general",
        bot_was_mentioned=True,
        ask_gsa_channel="ask-gsa",
    )
    assert result is True


def test_should_not_respond_unmentioned_other_channel(detector):
    result = detector.should_respond(
        message="what is GSA?",
        channel_name="general",
        bot_was_mentioned=False,
        ask_gsa_channel="ask-gsa",
    )
    assert result is False


def test_should_not_respond_bot_commands_channel_unmentioned(detector):
    result = detector.should_respond(
        message="some message",
        channel_name="bot-commands",
        bot_was_mentioned=False,
        ask_gsa_channel="ask-gsa",
    )
    assert result is False


# ── clean_message ─────────────────────────────────────────────────────────────

def test_clean_message_removes_bot_mention(detector):
    result = detector.clean_message(
        "<@123456789> what is 3MRP?",
        bot_mention_string="<@123456789>",
    )
    assert "123456789" not in result
    assert "what is 3MRP?" in result


def test_clean_message_removes_channel_refs(detector):
    result = detector.clean_message("check <#987654321> for details")
    assert "987654321" not in result


def test_clean_message_strips_whitespace(detector):
    result = detector.clean_message("   hello world   ")
    assert result == "hello world"


# ── Identity intent ───────────────────────────────────────────────────────────

def test_identity_who_are_you(detector):
    intent, conf = detector.detect("who are you")
    assert intent == INTENT_IDENTITY
    assert conf == 1.0


def test_identity_what_are_you(detector):
    intent, _ = detector.detect("what are you")
    assert intent == INTENT_IDENTITY


def test_identity_whats_your_name(detector):
    intent, _ = detector.detect("what's your name")
    assert intent == INTENT_IDENTITY


def test_identity_are_you_chatgpt(detector):
    intent, _ = detector.detect("are you chatgpt")
    assert intent == INTENT_IDENTITY


def test_identity_are_you_an_ai(detector):
    intent, _ = detector.detect("are you an ai")
    assert intent == INTENT_IDENTITY


def test_identity_what_model(detector):
    intent, _ = detector.detect("what model are you")
    assert intent == INTENT_IDENTITY


def test_identity_how_smart(detector):
    intent, _ = detector.detect("how smart are you")
    assert intent == INTENT_IDENTITY


def test_identity_does_not_shadow_help(detector):
    intent, _ = detector.detect("what can you do")
    assert intent == INTENT_HELP


def test_regular_question_not_identity(detector):
    intent, _ = detector.detect("what is the travel award?")
    assert intent == INTENT_QUESTION


# ── Free mode intent ──────────────────────────────────────────────────────────

def test_free_mode_trigger(detector):
    intent, conf = detector.detect("free mode")
    assert intent == INTENT_FREE_MODE
    assert conf == 1.0


def test_free_mode_exclamation(detector):
    intent, _ = detector.detect("!free")
    assert intent == INTENT_FREE_MODE


def test_general_mode_trigger(detector):
    intent, _ = detector.detect("general mode")
    assert intent == INTENT_FREE_MODE


def test_switch_to_free_trigger(detector):
    intent, _ = detector.detect("switch to free")
    assert intent == INTENT_FREE_MODE


def test_free_mode_not_confused_with_clear(detector):
    intent, _ = detector.detect("free mode")
    assert intent != INTENT_CLEAR_HISTORY


# ── GSA mode intent ───────────────────────────────────────────────────────────

def test_gsa_mode_trigger(detector):
    intent, conf = detector.detect("gsa mode")
    assert intent == INTENT_GSA_MODE
    assert conf == 1.0


def test_gsa_mode_exclamation(detector):
    intent, _ = detector.detect("!gsa")
    assert intent == INTENT_GSA_MODE


def test_switch_to_gsa_trigger(detector):
    intent, _ = detector.detect("switch to gsa")
    assert intent == INTENT_GSA_MODE


# ── Non-English greetings (Kavosh is multilingual-welcoming) ──────────────────

def test_greeting_persian_salam(detector):
    assert detector.detect("سلام")[0] == INTENT_GREETING


def test_greeting_spanish_hola(detector):
    assert detector.detect("hola")[0] == INTENT_GREETING


def test_greeting_chinese_nihao(detector):
    assert detector.detect("你好")[0] == INTENT_GREETING


# ── Identity intent: "introduce yourself" + cousins (accuracy backlog #7) ──────
@pytest.mark.parametrize("msg", [
    "Hi, introduce yourself thoroughly",
    "can you introduce yourself",
    "introduce yourself thoroughly please",
    "describe yourself",
    "tell me who you are",
])
def test_identity_introduce_and_cousins(detector, msg):
    intent, conf = detector.detect(msg)
    assert intent == INTENT_IDENTITY and conf == 1.0


@pytest.mark.parametrize("msg", [
    "how do I introduce myself at networking events",      # myself, not yourself
    "describe your research areas",                          # "your X", not "yourself"
    "describe the GSA funding process",
    "how do I present myself professionally at a career fair",
    "tips to present yourself in an interview",              # the dropped "present yourself" guard
])
def test_identity_does_not_over_trigger(detector, msg):
    intent, _ = detector.detect(msg)
    assert intent != INTENT_IDENTITY


def test_what_can_you_do_still_help(detector):
    intent, _ = detector.detect("what can you do")
    assert intent == INTENT_HELP

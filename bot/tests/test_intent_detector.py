"""Tests for the IntentDetector service."""

import pytest

from bot.services.intent_detector import (
    INTENT_CLEAR_HISTORY,
    INTENT_FOOD,
    INTENT_GREETING,
    INTENT_HELP,
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

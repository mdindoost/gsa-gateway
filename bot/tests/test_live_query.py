"""parse_explicit_live_search — deterministic 'search njit for X' detector.

Only fires on an EXPLICIT user request to go to the live njit.edu site (NOT free-text NLU),
so it's safe to run a live search directly. Returns the extracted topic X, or None.
"""
from bot.core.live_query import parse_explicit_live_search


def test_search_njit_for_topic():
    assert parse_explicit_live_search("search njit for library hours") == "library hours"


def test_search_njit_edu_for_topic():
    assert parse_explicit_live_search("search njit.edu for parking permits") == "parking permits"


def test_search_njit_website_for_topic():
    assert parse_explicit_live_search("search the njit website for tuition deadlines") == "tuition deadlines"


def test_look_up_topic_on_njit():
    assert parse_explicit_live_search("look up dining options on njit") == "dining options"


def test_check_njit_for_topic():
    assert parse_explicit_live_search("check njit for the academic calendar") == "the academic calendar"


def test_case_insensitive_and_trailing_punctuation():
    assert parse_explicit_live_search("Search NJIT for shuttle schedule?") == "shuttle schedule"


def test_non_search_question_returns_none():
    assert parse_explicit_live_search("who is the dean of YWCC") is None


def test_search_without_njit_returns_none():
    assert parse_explicit_live_search("search for a good restaurant near campus") is None


def test_empty_topic_returns_none():
    assert parse_explicit_live_search("search njit for") is None
    assert parse_explicit_live_search("search njit") is None

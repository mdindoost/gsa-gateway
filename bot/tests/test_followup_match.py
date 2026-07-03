from bot.core.pending import PendingOption
from bot.core.followup_match import match_followup, DECLINE


def _opts(*labels):
    return [PendingOption(l, "structured", {"skill": "x", "args": {}}) for l in labels]


ONE = _opts("most citations")
THREE = _opts("Ada Lovelace", "Alan Turing", "Grace Hopper")


def test_affirmation_single_option_selects_zero():
    for t in ["yes", "Yes.", "yeah", "yep", "sure", "ok", "okay", "yes please", "do it", "go ahead"]:
        assert match_followup(t, ONE) == 0, t


def test_affirmation_requires_whole_message():
    assert match_followup("yes but what about MTSM", ONE) is None
    assert match_followup("yes, who is the dean", ONE) is None


def test_negation_returns_decline():
    for t in ["no", "nope", "nah", "never mind", "no thanks"]:
        assert match_followup(t, ONE) is DECLINE, t


def test_affirmation_with_many_options_is_none():
    # "yes" to a pick-1-of-N is ambiguous -> None (never guess)
    assert match_followup("yes", THREE) is None


def test_ordinal_selection():
    assert match_followup("the first", THREE) == 0
    assert match_followup("2", THREE) == 1
    assert match_followup("2nd", THREE) == 1
    assert match_followup("#3", THREE) == 2
    assert match_followup("option 2", THREE) == 1
    assert match_followup("the first one", THREE) == 0
    assert match_followup("the fourth", THREE) is None   # out of range


def test_unique_label_selection():
    assert match_followup("Turing", THREE) == 1
    assert match_followup("Grace Hopper", THREE) == 2


def test_ambiguous_or_absent_label_is_none():
    assert match_followup("Smith", THREE) is None        # matches none
    assert match_followup("", ONE) is None
    assert match_followup("what are the office hours", ONE) is None

# v2/tests/test_deep_fallback_ladder.py
# Pure unit test of the adopt-if-better decision, extracted as a helper.
from bot.core.message_handler import _deep_adopt   # to be added in Step 4


def test_adopt_when_strictly_better_and_over_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.40, threshold=0.15) is True


def test_reject_when_not_better():
    assert _deep_adopt(current_rel=0.50, rescue_rel=0.40, threshold=0.15) is False


def test_reject_when_below_threshold():
    assert _deep_adopt(current_rel=0.05, rescue_rel=0.12, threshold=0.15) is False


def test_adopt_when_current_is_none_but_over_threshold():
    # relevance None => not a miss for the normal path, but if we got here (no chunks) adopt if >=T
    assert _deep_adopt(current_rel=None, rescue_rel=0.40, threshold=0.15) is True


def test_reject_when_rescue_rel_none():
    assert _deep_adopt(current_rel=0.10, rescue_rel=None, threshold=0.15) is False


def test_adopt_when_exactly_at_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.15, threshold=0.15) is True

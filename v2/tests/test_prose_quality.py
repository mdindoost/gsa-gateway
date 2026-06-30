"""Tests for the keep-fullest quality metric (day-1 rebuild Task 3)."""
from v2.core.ingestion.prose_quality import prose_quality_len, keep_better


def test_stripped_length_prefers_more_real_content():
    short = "Pay your bill online."
    long = short + " " + ("fee schedule details " * 40)
    assert prose_quality_len(long) > prose_quality_len(short)


def test_webpage_never_beats_policy():
    # a thin marketing 'webpage' must never supersede a substantive 'policy' row (the live
    # graduate-admissions trap), even when its raw bytes are larger
    assert keep_better("x" * 5000, "webpage", "y" * 10, "policy") is False
    assert keep_better("y" * 5000, "policy", "x" * 10, "webpage") is True


def test_density_breaks_within_same_type():
    assert keep_better("real " * 200, "policy", "nav " * 5, "policy") is True
    assert keep_better("nav " * 5, "policy", "real " * 200, "policy") is False

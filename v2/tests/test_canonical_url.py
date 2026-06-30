"""Tests for the one shared prose-identity normalizer (day-1 rebuild Task 1/2)."""
from v2.core.ingestion.canonical_url import canonical_prose_url as C, canonical_link


def test_trailing_slash_and_scheme():
    # http->https, lowercase host, strip trailing slash -> the two forms collapse to one identity
    assert C("http://WWW.njit.edu/registrar/") == "https://www.njit.edu/registrar"
    assert C("https://www.njit.edu/registrar") == "https://www.njit.edu/registrar"


def test_root_slash_kept():
    assert C("https://www.njit.edu/") == "https://www.njit.edu/"


def test_fragment_dropped_query_kept():
    # fragment is never page identity; query CAN be (e.g. ?audience=international) -> keep it
    assert C("https://x.njit.edu/p#sec") == "https://x.njit.edu/p"
    assert C("https://x.njit.edu/p?audience=international") == "https://x.njit.edu/p?audience=international"


def test_idempotent():
    u = "https://www.njit.edu/bursar/payment-information"
    assert C(C(u)) == C(u)


def test_canonical_link_present():
    html = ('<html><head><link rel="canonical" '
            'href="https://informatics.njit.edu/undergraduate-thesis-option"/></head></html>')
    assert canonical_link(html) == "https://informatics.njit.edu/undergraduate-thesis-option"


def test_canonical_link_absent():
    assert canonical_link("<html><head></head></html>") is None


def test_canonical_link_offsite_ignored():
    # a canonical pointing off njit.edu is not trusted to collapse our identity
    assert canonical_link('<link rel="canonical" href="https://example.com/x">') is None

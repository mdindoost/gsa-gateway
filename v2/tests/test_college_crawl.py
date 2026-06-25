"""TDD tests for college_crawl.py — Phase A prose engine."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.college_crawl import classify_type


def test_classify_type_by_url_path():
    assert classify_type("https://cs.njit.edu/news/award-2024") == "news"
    assert classify_type("https://cs.njit.edu/announcements/x") == "news"
    assert classify_type("https://computing.njit.edu/events/hackathon") == "event"
    assert classify_type("https://cs.njit.edu/academics/phd") == "policy"
    # segment match, not substring: a 'newsletter' page is not 'news'
    assert classify_type("https://cs.njit.edu/about/newsletter-signup") == "policy"


def test_is_people_path_segment_match():
    from v2.core.ingestion.college_crawl import is_people_path
    assert is_people_path("https://cs.njit.edu/faculty") is True
    assert is_people_path("https://cs.njit.edu/faculty/jane-doe") is True
    assert is_people_path("https://computing.njit.edu/people") is True
    assert is_people_path("https://cs.njit.edu/administration") is True
    # real prose that merely starts with the same letters must be KEPT:
    assert is_people_path("https://cs.njit.edu/faculty-handbook") is False
    assert is_people_path("https://cs.njit.edu/academics/phd") is False

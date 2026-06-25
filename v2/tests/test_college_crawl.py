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


def test_extract_dates_structured_only():
    from v2.core.ingestion.college_crawl import extract_dates
    html = '''
      <html><head>
        <meta property="article:published_time" content="2024-03-05T10:00:00Z">
        <script type="application/ld+json">
          {"@type":"Event","startDate":"2026-09-01","endDate":"2026-09-02"}
        </script>
      </head><body>
        <time datetime="2024-03-05">March 5, 2024</time>
        <p>Save the date next Friday</p>
      </body></html>'''
    d = extract_dates(html)
    assert d["published_at"] == "2024-03-05T10:00:00Z"
    assert d["event_start"] == "2026-09-01"
    assert d["event_end"] == "2026-09-02"


def test_extract_dates_absent_when_no_markup():
    from v2.core.ingestion.college_crawl import extract_dates
    # free text only — must NOT be parsed (mechanical-only hard line)
    assert extract_dates("<html><body><p>Event on Sept 1st</p></body></html>") == {}

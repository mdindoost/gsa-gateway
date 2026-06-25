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


import sqlite3
import json


def _conn():
    from v2.core.database.schema import create_all
    c = create_all(":memory:")
    # minimal org tree: njit -> ywcc
    from v2.core.graph.orgs import ensure_org
    ensure_org(c, "njit", "NJIT", None, type="university")
    ensure_org(c, "ywcc", "YWCC", "njit", type="college")
    return c


def test_ingest_college_types_dates_idempotent():
    from v2.core.ingestion.college_crawl import (
        ingest_college, EntryResult, PROSE_SOURCE)
    from v2.core.ingestion.eos_crawl import ProsePage
    c = _conn()
    page = ProsePage(title="CS News", content="Prof wins award.",
                     source_url="https://cs.njit.edu/news/award")
    res = EntryResult(seed="https://cs.njit.edu/", prose=[page], skipped=[])
    html_by_url = {"https://cs.njit.edu/news/award":
                   '<meta property="article:published_time" content="2024-03-05T00:00:00Z">'}
    out = ingest_college(c, "computer-science", "Computer Science", "ywcc", res, html_by_url)
    c.commit()
    row = c.execute("SELECT type, created_by, json_extract(metadata,'$.published_at') "
                    "FROM knowledge_items WHERE source_url=?",
                    ("https://cs.njit.edu/news/award",)).fetchone()
    assert tuple(row) == ("news", PROSE_SOURCE, "2024-03-05T00:00:00Z")
    # idempotent: re-ingest unchanged → no new active row
    ingest_college(c, "computer-science", "Computer Science", "ywcc", res, html_by_url)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND source_url=?",
                  ("https://cs.njit.edu/news/award",)).fetchone()[0]
    assert n == 1
    # no Person created from prose
    assert c.execute("SELECT COUNT(*) FROM nodes WHERE type='Person'").fetchone()[0] == 0


def test_prose_entry_points_registry():
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS, ProseEntry
    slugs = {e.org_slug for e in PROSE_ENTRY_POINTS}
    assert {"ywcc", "computer-science", "informatics", "data-science"} <= slugs
    for e in PROSE_ENTRY_POINTS:
        assert isinstance(e, ProseEntry)
        assert e.seed.startswith("https://") and e.seed.endswith("/")   # bare-host roots
    # NOT registered in the people registry
    from v2.core.ingestion import entry_points as ep
    people_urls = {p.url for p in ep.ALL_ENTRY_POINTS}
    assert all(e.seed not in people_urls for e in PROSE_ENTRY_POINTS)


def test_extract_entry_scopes_skips_people_dedups():
    from v2.core.ingestion.college_crawl import extract_entry
    pages = {
        "https://cs.njit.edu/": '<a href="/academics/phd">phd</a> <a href="/faculty">fac</a> '
                                '<a href="https://people.njit.edu/profile/x">x</a>'
                                '<h1>CS Home</h1><div role="main">Welcome to CS.</div>',
        "https://cs.njit.edu/academics/phd": '<h1>PhD</h1><div role="main">PhD in Computer Science requirements.</div>',
        "https://cs.njit.edu/faculty": '<h1>Faculty</h1><div role="main">Prof A. Prof B.</div>',
    }
    seen = []
    def fetch(u):
        seen.append(u)
        return pages.get(u)
    res = extract_entry("https://cs.njit.edu/", fetch, max_depth=3, budget=50)
    urls = {p.source_url for p in res.prose}
    assert "https://cs.njit.edu/academics/phd" in urls       # prose kept
    assert "https://cs.njit.edu/faculty" not in urls          # people page skipped
    assert all("people.njit.edu" not in u for u in seen)      # off-host never fetched

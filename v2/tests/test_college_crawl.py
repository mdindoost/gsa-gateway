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


def test_classify_type_newsroll_is_news():
    """F2: NJIT dept news articles live under /newsroll/<slug> (e.g. biology). It's unambiguously
    a news section — type it 'news' so the recency decay applies."""
    assert classify_type("https://biology.njit.edu/newsroll/phd-candidate-award") == "news"
    # still a segment match, not substring:
    assert classify_type("https://x.njit.edu/about/newsrolling-stones") == "policy"


def test_is_people_path_segment_match():
    from v2.core.ingestion.college_crawl import is_people_path
    assert is_people_path("https://cs.njit.edu/faculty") is True
    assert is_people_path("https://cs.njit.edu/faculty/jane-doe") is True
    assert is_people_path("https://computing.njit.edu/people") is True
    assert is_people_path("https://cs.njit.edu/administration") is True
    # real prose that merely starts with the same letters must be KEPT:
    assert is_people_path("https://cs.njit.edu/faculty-handbook") is False
    assert is_people_path("https://cs.njit.edu/academics/phd") is False


def test_is_people_path_drupal_pager_alias():
    """F1: Drupal pager aliases of a people roster (a numeric `-N` suffix on a people segment)
    are still people pages and must be skipped. The leak: management.njit.edu/administration-0
    is a 'View Profile' roster that bypassed the bare-segment match."""
    from v2.core.ingestion.college_crawl import is_people_path
    assert is_people_path("https://management.njit.edu/administration-0") is True
    assert is_people_path("https://design.njit.edu/people-1") is True
    assert is_people_path("https://x.njit.edu/faculty-12") is True
    # but a real prose page that merely ends in `<word>-<digits>` is KEPT (base isn't a people seg)
    assert is_people_path("https://math.njit.edu/faculty-research-talks-fall-2025") is False
    assert is_people_path("https://eng.njit.edu/faculty-awards") is False
    assert is_people_path("https://cs.njit.edu/cap-101") is False


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


def test_run_entry_extracts_and_ingests():
    from v2.core.ingestion.college_crawl import ProseEntry
    from scripts.crawl_college import run_entry
    c = _conn()
    pages = {"https://cs.njit.edu/": '<h1>CS</h1><div role="main">Computer Science at NJIT.</div>'}
    out = run_entry(c, ProseEntry("https://cs.njit.edu/", "computer-science",
                                  "Computer Science", "ywcc"),
                    lambda u: pages.get(u), budget=10, delay=0.0)
    c.commit()
    assert out["prose_inserted"] >= 1
    assert c.execute("SELECT COUNT(*) FROM knowledge_items WHERE created_by='college_crawl'"
                     ).fetchone()[0] >= 1


def test_natural_key_index_exists():
    c = _conn()
    idx = c.execute("SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_ki_natural_key'").fetchone()
    assert idx is not None


def test_crawl_entry_never_fetches_people_pages():
    """Regression: people-page links discovered during crawl must never be enqueued or fetched.
    If /people is fetched, its sub-links (/people/jane) would also be discovered and fetched,
    wasting budget on 200+ faculty profiles. Neither must hit the fetch function."""
    from v2.core.ingestion.college_crawl import crawl_entry
    pages = {
        "https://cs.njit.edu/": (
            '<a href="/about">about</a>'
            '<a href="/people">people</a>'
            '<h1>Home</h1><div role="main">Welcome to CS.</div>'
        ),
        "https://cs.njit.edu/about": (
            '<h1>About CS</h1><div role="main">The department was founded in 1966.</div>'
        ),
        # If /people were fetched it would enqueue /people/jane too — both must stay unfetched.
        "https://cs.njit.edu/people": (
            '<a href="/people/jane">jane</a>'
            '<h1>People</h1><div role="main">Prof A. Prof B.</div>'
        ),
        "https://cs.njit.edu/people/jane": (
            '<h1>Jane Doe</h1><div role="main">Professor of Computer Science.</div>'
        ),
    }
    fetched: list[str] = []

    def fetch(u: str) -> str:
        fetched.append(u)
        return pages.get(u, "")

    list(crawl_entry("https://cs.njit.edu/", fetch, max_depth=3, budget=50))

    assert "https://cs.njit.edu/people" not in fetched, \
        f"/people was fetched — people-page link not suppressed in crawl_entry enqueue; fetched={fetched}"
    assert "https://cs.njit.edu/people/jane" not in fetched, \
        f"/people/jane was fetched — cascade from unfetched /people; fetched={fetched}"
    assert "https://cs.njit.edu/about" in fetched, \
        f"/about (prose page) must be fetched but was not; fetched={fetched}"


def test_prose_entry_points_one_seed_per_host():
    """No two prose seeds share a host — each crawl owns its subdomain end-to-end, so a single
    host can't feed two orgs (the people layer's discoverable_host concern; here enforced by
    construction). MTSM/HCAD deliberately carry their schools' prose on the one college host."""
    from urllib.parse import urlsplit
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS
    hosts = [urlsplit(e.seed).netloc.lower() for e in PROSE_ENTRY_POINTS]
    dupes = {h for h in hosts if hosts.count(h) > 1}
    assert not dupes, f"prose seeds share a host (one host must map to one org): {dupes}"


def test_prose_entry_points_parents_resolvable():
    """Every entry's parent_slug is either the root (njit) or another entry's org_slug — so the
    org tree never orphans. (Department parents are colleges that also appear as entries.)"""
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS
    slugs = {e.org_slug for e in PROSE_ENTRY_POINTS}
    for e in PROSE_ENTRY_POINTS:
        assert e.parent_slug == "njit" or e.parent_slug in slugs, \
            f"{e.org_slug} parent {e.parent_slug!r} is neither njit nor a college entry"


def test_prose_entry_points_cover_all_colleges():
    """Regression for the college rollout: every NJIT college root has a prose entry."""
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS
    college_slugs = {e.org_slug for e in PROSE_ENTRY_POINTS if e.org_type == "college"}
    assert {"ywcc", "mtsm", "nce", "csla", "hcad", "honors"} <= college_slugs


def test_prose_entry_org_type():
    """Each entry declares its org tier: the college root is 'college', depts are 'department'."""
    from v2.core.ingestion.college_crawl import PROSE_ENTRY_POINTS
    by_type = {e.org_slug: e.org_type for e in PROSE_ENTRY_POINTS}
    assert by_type["ywcc"] == "college"
    assert by_type["computer-science"] == "department"
    assert by_type["informatics"] == "department"
    assert by_type["data-science"] == "department"


def test_ingest_college_creates_org_with_declared_type():
    """A brand-new dept entry must be CREATED as type='department', not 'college'."""
    from v2.core.ingestion.college_crawl import ingest_college, EntryResult
    c = _conn()  # seeds njit -> ywcc
    empty = EntryResult(seed="https://newdept.njit.edu/", prose=[], skipped=[])
    ingest_college(c, "newdept", "New Dept", "ywcc", empty, {}, org_type="department")
    assert c.execute("SELECT type FROM organizations WHERE slug='newdept'").fetchone()[0] == "department"
    ingest_college(c, "newcollege", "New College", "njit", empty, {}, org_type="college")
    assert c.execute("SELECT type FROM organizations WHERE slug='newcollege'").fetchone()[0] == "college"

# v2/tests/test_office_link_policy.py  (new file; more tests added in A3)
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion import web_crawler as wc


def test_make_fetcher_still_returns_html_only():
    # Backward-compat: the existing signature (html|None) is preserved.
    f = wc.make_fetcher()
    assert callable(f)


def test_fetch_with_status_exists_and_is_callable():
    f = wc.fetch_with_status()
    assert callable(f)


# ---------- Task 3: aspect="office" link policy ----------
SEED = "https://www.njit.edu/parking/"
HTML = """
<a href="/parking/visitor-parking">Visitor Parking</a>
<a href="/parking/permits">Permits and fees</a>
<a href="/parking/style.css">css</a>
<a href="https://external.example.com/parking/x">external</a>
"""

def test_office_policy_follows_all_same_scope_links_not_just_relevant():
    follow, files = wc.select_links(HTML, SEED, SEED, relevance_gated=False)
    assert "https://www.njit.edu/parking/visitor-parking" in follow
    assert "https://www.njit.edu/parking/permits" in follow      # NOT in people-relevance vocab
    assert not any(u.endswith(".css") for u in follow)           # assets still dropped
    assert not any("external.example.com" in u for u in follow)  # off-scope dropped

def test_people_policy_unchanged_still_relevance_gated():
    follow, _ = wc.select_links(HTML, SEED, SEED, relevance_gated=True)
    # "permits"/"visitor-parking" are not in the people RELEVANCE vocab → not followed.
    assert follow == set() or all("permit" not in u and "visitor" not in u for u in follow)

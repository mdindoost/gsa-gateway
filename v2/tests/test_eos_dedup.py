"""TDD for the two issues the live dry-run exposed:
  1. http:// vs https:// duplicates (http links returned the home-page stub).
  2. legacy .php URL aliases that serve identical content to the clean URL.
Fix: canonicalize scheme to https; dedup prose by content hash, preferring the clean URL.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_entry

FX = Path(__file__).parent / "fixtures" / "eos"
SEED = "https://www.njit.edu/parking/"
VISITOR_HTML = (FX / "visitor_parking.html").read_text()


def _fetch(pages):
    return lambda url: pages.get(url)


def test_canonicalizes_http_links_to_https():
    hub = '<div role="main"><a href="http://www.njit.edu/parking/visitor-parking">V</a></div>'
    pages = {SEED: hub, "https://www.njit.edu/parking/visitor-parking": VISITOR_HTML}
    res = extract_entry(SEED, _fetch(pages))
    urls = {p.source_url for p in res.prose}
    assert "https://www.njit.edu/parking/visitor-parking" in urls
    assert all(u.startswith("https://") for u in urls)


def test_dedups_identical_content_prefers_clean_url():
    hub = (
        '<div role="main">'
        '<a href="/parking/visitor-parking">clean</a>'
        '<a href="/parking/visitor-guide.php">legacy</a>'
        "</div>"
    )
    pages = {
        SEED: hub,
        "https://www.njit.edu/parking/visitor-parking": VISITOR_HTML,
        "https://www.njit.edu/parking/visitor-guide.php": VISITOR_HTML,
    }
    res = extract_entry(SEED, _fetch(pages))
    vp = [p for p in res.prose if p.title == "Visitor Parking"]
    assert len(vp) == 1
    assert vp[0].source_url.endswith("/visitor-parking")  # clean preferred over .php

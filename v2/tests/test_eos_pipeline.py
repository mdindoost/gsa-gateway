"""TDD for the EOS DFS crawl pipeline: seed -> follow same-scope links deep ->
classify + extract each page into staff (KG) + prose (KB) + skipped.

Uses a dict-backed fetcher over the REAL page fixtures (the pattern crawl_site tests
use) so the traversal + classify + extract integration is exercised end-to-end.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_entry

FX = Path(__file__).parent / "fixtures" / "eos"
SEED = "https://www.njit.edu/parking/"
CONTACTS = SEED + "facilities-systems-contacts"
VISITOR = SEED + "visitor-parking"

# minimal hub that links (same-scope) to the two real sub-pages + one off-scope nav link
HUB = (
    '<html><body><div role="main">'
    f'<a href="/parking/facilities-systems-contacts">Contact Us</a>'
    f'<a href="/parking/visitor-parking">Visitor Parking</a>'
    f'<a href="/bursar">Tuition</a>'  # off-scope: must NOT be followed
    "</div></body></html>"
)


def _fetch(pages):
    return lambda url: pages.get(url)


def _result():
    pages = {
        SEED: HUB,
        CONTACTS: (FX / "contacts.html").read_text(),
        VISITOR: (FX / "visitor_parking.html").read_text(),
    }
    return extract_entry(SEED, _fetch(pages))


def test_pipeline_collects_five_staff():
    assert len(_result().staff) == 5


def test_pipeline_collects_visitor_prose():
    titles = {p.title for p in _result().prose}
    assert "Visitor Parking" in titles


def test_pipeline_stays_in_scope():
    # /bursar is off-scope and (in this fixture set) unfetchable; it must never appear.
    urls = {p.source_url for p in _result().prose}
    assert all("/bursar" not in u for u in urls)

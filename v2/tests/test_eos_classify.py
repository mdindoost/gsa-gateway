"""TDD for the EOS page classifier: staff-roster / prose / skip-empty.

Roster takes precedence (the contacts page also has address prose, but it's the people
source). A page with no readable main content (true JS-only shell) is skip-empty so the
caller flags it rather than storing an empty page (anti-fab).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import classify_page

FX = Path(__file__).parent / "fixtures" / "eos"


def test_classify_contacts_page_is_staff_roster():
    assert classify_page((FX / "contacts.html").read_text()) == "staff-roster"


def test_classify_service_page_is_prose():
    assert classify_page((FX / "visitor_parking.html").read_text()) == "prose"


def test_classify_empty_shell_is_skip():
    empty = "<html><body><div role='main'></div></body></html>"
    assert classify_page(empty) == "skip-empty"

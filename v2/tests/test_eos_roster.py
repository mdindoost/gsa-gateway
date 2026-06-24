"""TDD for the EOS contacts-page roster parser.

Fixture is the REAL fetched page (v2/tests/fixtures/eos/contacts.html) so the test
pins extraction against the actual NJIT markup, including the Erixson email that the
source splits across two lines.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion.eos_crawl import parse_roster

FIXTURE = Path(__file__).parent / "fixtures" / "eos" / "contacts.html"


def _staff():
    return parse_roster(clean_text(FIXTURE.read_text()))


def test_parse_roster_extracts_exactly_five_staff():
    assert len(_staff()) == 5


def test_parse_roster_fields_for_avp():
    g = {s.email: s for s in _staff()}["gjini@njit.edu"]
    assert g.name == "Robert N. Gjini"
    assert g.title == "Assistant Vice President"
    assert g.phone == "973-642-7190"


def test_parse_roster_rejoins_split_email():
    # Erixson's address is split across two lines in the source; parser must rejoin it.
    by_email = {s.email: s for s in _staff()}
    assert "christopher.a.erixson@njit.edu" in by_email
    e = by_email["christopher.a.erixson@njit.edu"]
    assert e.name == "Christopher A. Erixson"
    assert e.title == "Coordinator"


def test_parse_roster_all_emails():
    assert {s.email for s in _staff()} == {
        "gjini@njit.edu",
        "richard.mendez@njit.edu",
        "mikedab@njit.edu",
        "guillen@njit.edu",
        "christopher.a.erixson@njit.edu",
    }

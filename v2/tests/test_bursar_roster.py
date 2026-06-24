from pathlib import Path

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import bursar_crawl as bc

FIX = Path(__file__).parent / "fixtures" / "bursar"


def test_contact_us_has_no_personnel_so_zero_people():
    """Bursar's contact-us is office-level only (no named staff). The real fixture must not even
    contain the 'personnel' anchor, so parse_roster returns [] with no fabricated people."""
    text = clean_text((FIX / "contact-us.html").read_text(encoding="utf-8"))
    assert "personnel" not in text.lower()               # premise pinned to the real page
    staff, warnings = bc.parse_roster(text)
    assert staff == [] and warnings == []


def test_function_email_never_becomes_a_person():
    """NEGATIVE regression (RAG-review B1): even if a future page introduces the 'personnel'
    anchor next to an office-label line + phone + a DEPARTMENTAL (function) email, the guard
    blocks fabrication — no Person named 'Student Accounts' from bursar@njit.edu."""
    text = ("Personnel\n"
            "Student Accounts\n"
            "Office of the Bursar\n"
            "973-596-2877\n"
            "bursar@njit.edu\n")
    staff, warnings = bc.parse_roster(text)
    assert staff == []                                   # function email → not a person
    assert any("bursar@njit.edu" in w for w in warnings)  # surfaced, never silently dropped


def test_genuine_person_email_still_parses():
    """The guard blocks only function mailboxes — a real personal njit.edu address with a
    cued title still yields a Person (so a future named roster is not lost)."""
    text = ("Personnel\n"
            "Jane Doe\n"
            "Assistant Director\n"
            "973-596-1234\n"
            "jane.doe@njit.edu\n")
    staff, warnings = bc.parse_roster(text)
    assert [s.name for s in staff] == ["Jane Doe"]
    assert staff[0].email == "jane.doe@njit.edu"

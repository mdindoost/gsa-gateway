from dataclasses import fields
from pathlib import Path
from v2.core.ingestion import ist_crawl
from v2.core.ingestion.ist_crawl import StaffRecord

FIX = Path(__file__).parent / "fixtures" / "ist"


def test_staffrecord_has_no_contact_fields():
    # Positive structural check (replaces the old tautological hasattr assertion).
    assert {f.name for f in fields(StaffRecord)} == {"name", "title", "unit"}


def test_parse_key_contacts():
    html = (FIX / "key_contacts.html").read_text(encoding="utf-8")
    staff, warnings = ist_crawl.parse_roster(ist_crawl._clean_main(html))
    by_name = {s.name: s for s in staff}
    # Name reformatted "Last, First" -> "First Last"
    assert "Blake Haggerty" in by_name
    assert by_name["Blake Haggerty"].title.startswith("Interim Vice President")
    assert "Anthony Farber" in by_name
    assert by_name["Anthony Farber"].title == "Assistant Director IST Service Desk"
    # unit is a real section header, never page chrome (F2)
    assert by_name["Anthony Farber"].unit == "Digital Learning and Campus Support"
    for s in staff:
        assert s.unit.lower() not in ("popular searches", "search", "home", "about",
                                      "skip to main content", "view profile", "")
    assert len(by_name) >= 12


def test_delimiter_anchor_keeps_particle_surname():
    # F1: a lowercase-particle surname must NOT be silently dropped.
    text = ("Enterprise Applications\n"
            "van der Berg, Jan\nDirector, Data Platforms\nView Profile\n")
    staff, warnings = ist_crawl.parse_roster(text + text)  # >=2 delimiters → recognized page
    names = {s.name for s in staff}
    assert "Jan van der Berg" in names


def test_nonroster_page_returns_empty():
    staff, warnings = ist_crawl.parse_roster("Software Availability\nIST provides software.")
    assert staff == [] and warnings == []

from pathlib import Path

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import registrar_crawl as rc

FIX = Path(__file__).parent / "fixtures" / "registrar"


def _roster():
    text = clean_text((FIX / "staff.html").read_text(encoding="utf-8"))
    return rc.parse_roster(text)


def test_staff_page_yields_all_thirteen_named_staff_no_fabrication():
    """The real Registrar Staff table (Name / Phone / Functions) yields exactly 13 people,
    names normalized 'Last, First' -> 'First Last', phones dash-normalized, and zero warnings
    (every row parses cleanly). parse_roster works on cleaned TEXT, so email is empty here — the
    mailto emails are attached later from raw HTML by extract_entry (see test_registrar_emails)."""
    staff, warnings = _roster()
    assert warnings == []
    names = [s.name for s in staff]
    assert names == [
        "Jerry Trombella", "Allison Babinski", "Jeffrey Beatty", "Lorin Castellanos",
        "Joslyne Contreras", "Niki Gardiner", "Tiana Harrington", "Cecille Herrera",
        "Nathanielle Louis", "Diane McKeown", "Fatima Rivera", "Lea Ronchi", "Maryann Sawka",
    ]
    assert all(s.email == "" for s in staff)           # text layer carries no email; HTML does


def test_multi_token_surname_is_captured_not_dropped():
    """S1: a real staffer whose surname has spaces ('Van Pelt') must be captured, not silently
    lost — every pre-comma token is capitalized, so it is a name, while a comma-title is not."""
    text = "Name\nPhone\nFunctions\nVan Pelt, Bob\n973 596 1234\nRecords Clerk\n"
    staff, warnings = rc.parse_roster(text)
    assert warnings == []
    assert [s.name for s in staff] == ["Bob Van Pelt"]
    assert staff[0].title == "Records Clerk"


def test_duplicate_name_warns_never_silent_drops():
    """S2: two rows that normalize to the same name keep the first AND emit a warning (the module
    promises 'never silent-drop'), rather than dropping the second with no record."""
    text = ("Name\nPhone\nFunctions\n"
            "Smith, John\n973 596 1111\nClerk A\n"
            "Smith, John\n973 596 2222\nClerk B\n")
    staff, warnings = rc.parse_roster(text)
    assert [s.name for s in staff] == ["John Smith"]
    assert any("homonym" in w or "duplicate" in w for w in warnings)


def test_title_containing_a_phone_is_not_truncated():
    """N1: a title that merely CONTAINS a phone number is not mistaken for a record boundary."""
    text = "Name\nPhone\nFunctions\nDoe, Jane\n973 596 3000\nCall 973-596-9999 for transcripts\n"
    staff, warnings = rc.parse_roster(text)
    assert warnings == []
    assert staff[0].title == "Call 973-596-9999 for transcripts"


def test_first_record_fields():
    staff, _ = _roster()
    top = staff[0]
    assert top.name == "Jerry Trombella"
    assert top.title == "University Registrar"
    assert top.phone == "973-596-3236"                 # space format normalized to dashes
    assert top.titles == ("University Registrar",)


def test_cue_less_title_is_captured_positionally():
    """McKeown's title 'Enrollment Certification' carries no role-cue word; the table parser is
    POSITIONAL (name / phone / title) so it captures the title regardless of cue vocabulary."""
    staff, _ = _roster()
    mck = next(s for s in staff if s.name == "Diane McKeown")
    assert mck.title == "Enrollment Certification"


def test_comma_bearing_title_is_not_split_into_a_person():
    """Babinski's title contains a comma ('...Graduation, Veterans and Military Affairs'); the
    name-detector requires a SINGLE-token surname before the comma, so the title is never
    mistaken for the next person's name (anti-fab) and stays attached to its owner."""
    staff, _ = _roster()
    bab = next(s for s in staff if s.name == "Allison Babinski")
    assert bab.title == "Asst. Registrar for Graduation, Veterans and Military Affairs"
    assert not any("Veterans" in s.name for s in staff)


def test_non_roster_page_returns_empty():
    """A normal service page (no Name/Phone/Functions header) yields no people — falls to prose."""
    text = clean_text((FIX / "withdrawal.html").read_text(encoding="utf-8"))
    assert rc.parse_roster(text) == ([], [])


def test_office_label_without_comma_never_becomes_a_person():
    """NEGATIVE anti-fab: even inside a roster table, a line with no 'Surname,' shape (an office
    label like 'Student Accounts') is never fabricated into a Person; the row warns instead."""
    text = "Name\nPhone\nFunctions\nStudent Accounts\n973 596 2877\nBilling Office\n"
    staff, warnings = rc.parse_roster(text)
    assert staff == []
    assert warnings        # flagged, not silently dropped, never fabricated

from pathlib import Path

from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_parses_named_staff_with_inline_contact():
    text = clean_text((FIX / "contact.php.html").read_text(encoding="utf-8"))
    staff, warnings = gc.parse_roster(text)
    by = {s.name: s for s in staff}

    z = by["Sotirios G. Ziavras, D.Sc."]
    assert z.title == "Vice Provost for Graduate Studies and Dean of the Graduate Faculty"
    assert z.phone == "973-596-3462"
    assert z.email == "ziavras@njit.edu"
    assert z.unit == ""                                   # top group, no section header

    c = by["Cortney Wortman"]
    assert c.title == "Coordinator (Graduate Awards)"
    assert c.email == "wortman@njit.edu"
    assert c.unit == "Graduate Student Awards"            # section header captured, NOT the name

    # section header propagates to ALL its members, not just the first (header shown once)
    assert by["Maria Lirio P. Macklin"].unit == "Graduate Student Awards"
    assert by["Angela Retino"].unit == ""                 # before any header → no unit

    assert "Graduate Student Awards" not in by            # header never became a person
    assert by["Clarisa González-Lenahan"].email == "clarisa.gonzalez-lenahan@njit.edu"  # accents kept


def test_non_roster_page_yields_nothing():
    text = clean_text((FIX / "phd_credit.html").read_text(encoding="utf-8"))
    staff, warnings = gc.parse_roster(text)
    assert staff == [] and warnings == []

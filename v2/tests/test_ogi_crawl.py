"""Tests for the Office of Global Initiatives (OGI) crawler (Crawling 2.1, office #3).

Fixture: ogi_staff.html — the /office-global-initiatives-staff page (8 people, 'View Profile'-anchored).
Design-delta: docs/superpowers/specs/2026-06-24-ogi-crawl-design.md
"""
from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion import ogi_crawl as O
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def _staff_text():
    html = (FIX / "ogi_staff.html").read_text(encoding="utf-8")
    return clean_text(str(O._main_region(BeautifulSoup(html, "html.parser"))))


def test_parses_all_8_people():
    records, warnings = O.parse_roster(_staff_text())
    assert len(records) == 8, [r.name for r in records]
    assert warnings == [], warnings


def test_every_person_has_email_title():
    records, _ = O.parse_roster(_staff_text())
    for r in records:
        assert r.email.endswith("@njit.edu"), r
        assert r.title, r
        assert r.unit == O.OGI_NAME


def test_specific_people_and_middle_initials():
    records, _ = O.parse_roster(_staff_text())
    by = {r.name: r for r in records}
    assert by["Marieta Chemishanova"].email == "mpc33@njit.edu"
    # title is the verbatim detail-block title (not the summary card variant)
    assert by["Marieta Chemishanova"].title == "Executive Director"
    assert by["James A Jones"].title == \
        "Associate Director for International Faculty and Scholars, Office of Global Initiatives"
    # middle-initial names must parse
    assert by["James A Jones"].email == "james.jones@njit.edu"
    assert by["Vaughn C. Rogers"].email == "vaughn.rogers@njit.edu"
    # phone captured (line after email)
    assert by["Rebecca Wolk"].phone == "973-596-3195"
    assert by["Nadine Hawkins"].email == "nadine.hawkins@njit.edu"


def test_function_mailbox_and_summary_cards_not_people():
    records, _ = O.parse_roster(_staff_text())
    assert "global@njit.edu" not in {r.email for r in records}
    # section headers / 'Executive Director' label not minted as a person name
    assert "Executive Director" not in {r.name for r in records}


def test_non_staff_page_yields_no_people_via_gate():
    # an arbitrary /global page with a function mailbox must not mint people through extract_entry
    staff_html = (FIX / "ogi_staff.html").read_text(encoding="utf-8")
    other = ("<html><body><div role='main'><h1>OPT</h1>"
             "<p>Questions? email global@njit.edu</p></div></body></html>")
    pages = {
        "https://www.njit.edu/global/": "<html><body><div role='main'><h1>Global</h1>"
            "<a href='/global/office-global-initiatives-staff'>staff</a>"
            "<a href='/global/optional-practical-training'>opt</a></div></body></html>",
        "https://www.njit.edu/global/office-global-initiatives-staff": staff_html,
        "https://www.njit.edu/global/optional-practical-training": other,
    }
    res = O.extract_entry("https://www.njit.edu/global/", lambda u: pages.get(u), budget=50)
    assert len(res.staff) == 8, [s.name for s in res.staff]
    assert any("optional-practical-training" in p.source_url for p in res.prose)


def test_diacritic_names_parse():
    # a future hire with a diacritic must parse, not be silent-dropped (complete-coverage)
    text = "View Profile\nJosé Álvarez\nInternational Adviser\njose.alvarez@njit.edu\n973-596-0000\n"
    records, warnings = O.parse_roster(text)
    assert [r.name for r in records] == ["José Álvarez"]
    assert records[0].email == "jose.alvarez@njit.edu"
    assert warnings == []


def test_recount_warns_on_structure_change():
    # a 'View Profile' block whose email is missing -> fewer people than emails (none here) is fine,
    # but a personal email outside any block should trigger the recount warning
    text = "View Profile\nJane Doe\nAdvisor\njd5@njit.edu\nStray\nstray.person@njit.edu\n"
    records, warnings = O.parse_roster(text)
    assert [r.name for r in records] == ["Jane Doe"]
    assert any("possible roster-structure change" in w for w in warnings)


def test_ingest_writes_people_and_prose(tmp_path):
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    staff_html = (FIX / "ogi_staff.html").read_text(encoding="utf-8")
    def fetch(url):
        return staff_html if url.endswith("office-global-initiatives-staff") else None
    res = O.extract_entry("https://www.njit.edu/global/office-global-initiatives-staff", fetch, budget=5)
    summary = O.ingest_ogi(conn, res)
    conn.commit()
    assert summary["staff"] == 8
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND key LIKE 'crawler/ogi/%'").fetchone()[0]
    assert n == 8

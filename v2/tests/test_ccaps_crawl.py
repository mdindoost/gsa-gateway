"""Tests for the Counseling Center (C-CAPS) crawler (Crawling 2.1, office #6).

Fixture: ccaps_staff.html — /counseling/c-caps-staff (7 people; email-anchored, credential suffix,
Phone:/Email: labels). Design-delta: docs/superpowers/specs/2026-06-24-ccaps-crawl-design.md
"""
from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion import ccaps_crawl as C
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def _staff_text():
    html = (FIX / "ccaps_staff.html").read_text(encoding="utf-8")
    return clean_text(str(C._main_region(BeautifulSoup(html, "html.parser"))))


def test_parses_all_7_people():
    records, warnings = C.parse_roster(_staff_text())
    assert len(records) == 7, [r.name for r in records]
    assert warnings == [], warnings


def test_credential_stripped_from_name_titles_and_phone_kept():
    records, _ = C.parse_roster(_staff_text())
    by = {r.name: r for r in records}
    # credential ('Ph.D.') stripped from the stored name
    assert "Phyllis Bolling" in by, list(by)
    b = by["Phyllis Bolling"]
    assert b.email == "phyllis.bolling@njit.edu"
    assert b.titles[0] == "Director"
    assert "Licensed Psychologist" in b.titles            # multiple title lines kept
    assert "596-3420" in b.phone                           # phone captured from 'Phone:' line
    # a name without a credential (no comma) parses too
    assert by["Yessenia Rivera"].email == "yessenia.rivera@njit.edu"
    # multi-credential name ('Maham Tariq, MA, LPC') -> name is just 'Maham Tariq'
    assert "Maham Tariq" in by
    assert by["Maham Tariq"].email == "maham.tariq@njit.edu"


def test_labels_and_function_mailbox_not_people():
    records, _ = C.parse_roster(_staff_text())
    names = {r.name for r in records}
    for bad in ("Our Staff", "C-CAPS Staff", "Director", "Licensed Psychologist", "Email"):
        assert bad not in names
    for r in records:
        assert not r.title.lower().startswith("phone:")
        assert r.title.lower() != "email:"


def test_extract_entry_url_gated(tmp_path):
    staff_html = (FIX / "ccaps_staff.html").read_text(encoding="utf-8")
    other = ("<html><body><div role='main'><h1>Crisis</h1>"
             "<p>Call 973-596-3414 or email counseling@njit.edu</p></div></body></html>")
    pages = {
        "https://www.njit.edu/counseling/": "<html><body><div role='main'><h1>C</h1>"
            "<a href='/counseling/c-caps-staff'>s</a><a href='/counseling/get-help-247'>h</a></div></body></html>",
        "https://www.njit.edu/counseling/c-caps-staff": staff_html,
        "https://www.njit.edu/counseling/get-help-247": other,
    }
    res = C.extract_entry("https://www.njit.edu/counseling/", lambda u: pages.get(u), budget=20)
    assert len(res.staff) == 7, [s.name for s in res.staff]
    assert any("get-help-247" in p.source_url for p in res.prose)


def test_ingest_writes_people_and_prose(tmp_path):
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    staff_html = (FIX / "ccaps_staff.html").read_text(encoding="utf-8")
    def fetch(url):
        return staff_html if url.endswith("/c-caps-staff") else None
    res = C.extract_entry("https://www.njit.edu/counseling/c-caps-staff", fetch, budget=5)
    summary = C.ingest_ccaps(conn, res)
    conn.commit()
    assert summary["staff"] == 7
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND key LIKE 'crawler/counseling/%'").fetchone()[0]
    assert n == 7


def test_name_shaped_section_header_not_minted_as_person():
    # a name-shaped, role-keyword-free section header before the real name must NOT be the person
    text = "\n".join(["Wellness Program", "Jane Doe", "Staff Clinician",
                       "Phone: 973-596-0000", "Email:", "jane.doe@njit.edu"])
    records, warnings = C.parse_roster(text)
    assert [r.name for r in records] == ["Jane Doe"]
    assert records[0].email == "jane.doe@njit.edu"

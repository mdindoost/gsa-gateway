"""Tests for the Dean of Students (DOS) crawler (Crawling 2.1, office #5).

Fixture: dos_contact.html — /dos/contact.php (7 people; 'View Profile'-terminated blocks,
'Surname, Given' names, NO per-person email). Design-delta: docs/superpowers/specs/2026-06-24-dos-crawl-design.md
"""
from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion import dos_crawl as D
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def _contact_text():
    html = (FIX / "dos_contact.html").read_text(encoding="utf-8")
    return clean_text(str(D._main_region(BeautifulSoup(html, "html.parser"))))


def test_parses_all_7_people():
    records, warnings = D.parse_roster(_contact_text())
    assert len(records) == 7, [r.name for r in records]
    assert warnings == [], warnings


def test_names_reordered_and_titles_attached():
    records, _ = D.parse_roster(_contact_text())
    by = {r.name: r for r in records}
    assert "Marybeth Boger" in by                       # 'Boger, Marybeth' -> reordered
    assert by["Marybeth Boger"].title == "Senior Vice President of Student Affairs and Dean of Students"
    assert by["Marybeth Boger"].unit == "Senior Vice President for Student Affairs and Dean of Students"
    assert "Sean Dowd" in by
    assert "Kristie Damell" in by
    # the last person shares the prior 'Administrative Staff' header (headerless block)
    assert "Shyron Edwards" in by
    assert by["Shyron Edwards"].title == "Administrative Manager"
    assert by["Shyron Edwards"].unit == "Administrative Staff"


def test_no_email_published():
    records, _ = D.parse_roster(_contact_text())
    assert all(r.email == "" for r in records)


def test_title_with_comma_not_mistaken_for_name():
    # Rodgers' title 'Executive Assistant, Dean of Students...' has a comma but is NOT a name
    records, _ = D.parse_roster(_contact_text())
    names = {r.name for r in records}
    assert "Shakera Rodgers" in names
    for bad in ("Dean Executive Assistant", "Students And Campus Life Executive Assistant"):
        assert bad not in names
    # no section header / title minted as a person
    assert "Administrative Staff" not in names


def test_non_contact_page_no_people_and_recount(tmp_path):
    # an arbitrary /dos page (no View Profile roster) -> 0 people, falls through to prose
    records, warnings = D.parse_roster("Academic Integrity\nNJIT seeks to create a just campus.\n")
    assert records == []


def test_ingest_writes_people_and_prose(tmp_path):
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    contact = (FIX / "dos_contact.html").read_text(encoding="utf-8")
    def fetch(url):
        return contact if url.endswith("/contact.php") else None
    res = D.extract_entry("https://www.njit.edu/dos/contact.php", fetch, budget=5)
    summary = D.ingest_dos(conn, res)
    conn.commit()
    assert summary["staff"] == 7
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND key LIKE 'crawler/dean-of-students/%'").fetchone()[0]
    assert n == 7


def test_multi_name_block_does_not_swallow_second_name_into_title():
    # anomaly: two name cards in one block -> first kept, its title truncated before the 2nd name
    text = "\n".join(["Director", "Doe, Jane", "Director of Conduct",
                      "Smith, John", "Associate Director", "View Profile"])
    records, warnings = D.parse_roster(text)
    assert records[0].name == "Jane Doe"
    assert records[0].title == "Director of Conduct"      # 'Smith, John' NOT swallowed
    assert any("multiple names in one block" in w for w in warnings)


def test_people_gate_exact_path_only():
    contact = (FIX / "dos_contact.html").read_text(encoding="utf-8")
    nested = ("<html><body><div role='main'><h1>TIX</h1>"
              "Director<br>Doe, Jane<br>Coordinator<br>View Profile</div></body></html>")
    pages = {
        "https://www.njit.edu/dos/": "<html><body><div role='main'><h1>DOS</h1>"
            "<a href='/dos/contact.php'>c</a><a href='/dos/titleix/contact.php'>t</a></div></body></html>",
        "https://www.njit.edu/dos/contact.php": contact,
        "https://www.njit.edu/dos/titleix/contact.php": nested,   # nested contact.php must NOT gate people
    }
    res = D.extract_entry("https://www.njit.edu/dos/", lambda u: pages.get(u), budget=20)
    assert len(res.staff) == 7, [s.name for s in res.staff]   # only the real /dos/contact.php

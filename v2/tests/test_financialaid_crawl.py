"""Tests for the Office of Financial Aid crawler (Crawling 2.1, office #4) — PROSE-ONLY office.

Financial Aid publishes no named-staff roster (Bursar pattern): 0 KG people is the correct outcome,
and the function-mailbox guard keeps it robust. Fixture: financialaid_contact.html (the contact-us page).
Design-delta: docs/superpowers/specs/2026-06-24-financialaid-crawl-design.md
"""
from pathlib import Path

from v2.core.ingestion import financialaid_crawl as F
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def test_contact_page_yields_no_people():
    html = (FIX / "financialaid_contact.html").read_text(encoding="utf-8")
    records, warnings = F.parse_roster(clean_text(html))
    assert records == [], records          # no roster -> no people (prose-only office)


def test_contact_page_is_prose():
    html = (FIX / "financialaid_contact.html").read_text(encoding="utf-8")
    page = F.extract_prose("https://www.njit.edu/financialaid/contact-us", html)
    assert page is not None
    assert "finaid@njit.edu" in page.content          # inline office contact preserved verbatim
    assert "973-596-3479" in page.content


def test_function_mailbox_never_a_person():
    # even if a future page introduces a 'personnel' block next to a departmental mailbox,
    # the function-mailbox guard must refuse to fabricate a person (warns instead)
    text = "Personnel\nFinancial Aid Office\nDirector\n973-596-0000\nfinaid@njit.edu\n"
    records, warnings = F.parse_roster(text)
    assert records == []
    assert any("function mailbox" in w for w in warnings)


def test_real_named_person_in_personnel_block_parses():
    # the inherited Bursar parser DOES mint a real named person if a true Personnel block ever appears
    text = "Personnel\nJane Doe\nFinancial Aid Counselor\n973-596-1234\njane.doe@njit.edu\n"
    records, warnings = F.parse_roster(text)
    assert len(records) == 1
    assert records[0].name == "Jane Doe"
    assert records[0].email == "jane.doe@njit.edu"


def test_extract_entry_prose_only(tmp_path):
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    contact = (FIX / "financialaid_contact.html").read_text(encoding="utf-8")
    pages = {
        "https://www.njit.edu/financialaid/": "<html><body><div role='main'><h1>FA</h1>"
            "<p>Aid info</p><a href='/financialaid/contact-us'>contact</a></div></body></html>",
        "https://www.njit.edu/financialaid/contact-us": contact,
    }
    res = F.extract_entry("https://www.njit.edu/financialaid/", lambda u: pages.get(u), budget=10)
    assert res.staff == []                              # prose-only -> 0 staff
    assert len(res.prose) >= 1
    summary = F.ingest_financialaid(conn, res)
    conn.commit()
    assert summary["staff"] == 0
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person'").fetchone()[0]
    assert n == 0

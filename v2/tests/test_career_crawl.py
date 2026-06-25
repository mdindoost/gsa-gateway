"""Tests for the Career Development Services crawler (Crawling 2.1, office #7, last).

Same roster shape as DOS (View-Profile-terminated, 'Surname, Given', no email). Some staff are
cross-listed under two teams (deduped by name). Fixture: career_contact.html.
Design-delta: docs/superpowers/specs/2026-06-24-career-crawl-design.md
"""
from pathlib import Path

from bs4 import BeautifulSoup

from v2.core.ingestion import career_crawl as C
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def _contact_text():
    html = (FIX / "career_contact.html").read_text(encoding="utf-8")
    return clean_text(str(C._main_region(BeautifulSoup(html, "html.parser"))))


def test_parses_16_unique_people_with_crosslist_dedup():
    records, warnings = C.parse_roster(_contact_text())
    assert len(records) == 16, [r.name for r in records]
    # cross-listed people deduped; recount must NOT warn (accounts for cross-listings)
    assert not any("roster-structure change" in w for w in warnings), warnings
    assert sum("cross-listed" in w for w in warnings) == 2   # Lanzot, Sims


def test_names_reordered_titles_sections():
    records, _ = C.parse_roster(_contact_text())
    by = {r.name: r for r in records}
    assert by["Patrick Young"].title == "Executive Director, Career Development Services"
    assert by["Patrick Young"].unit == "Executive Director"
    # multi-token surname reordered
    assert "Carolina Barba Granda" in by
    assert by["Carolina Barba Granda"].unit == "Student Success / Career Advising"
    # section-header persistence (Perez inherits 'Leadership')
    assert by["Nayelli Perez"].unit == "Leadership"
    assert all(r.email == "" for r in records)


def test_no_title_or_header_minted_as_person():
    records, _ = C.parse_roster(_contact_text())
    names = {r.name for r in records}
    for bad in ("Leadership", "Employer Relations", "Operations Technology",
                "Student Success Career Advising"):
        assert bad not in names


def test_extract_entry_url_gated():
    contact = (FIX / "career_contact.html").read_text(encoding="utf-8")
    pages = {
        "https://www.njit.edu/careerservices/": "<html><body><div role='main'><h1>CDS</h1>"
            "<a href='/careerservices/contact-us'>c</a><a href='/careerservices/career-fairs'>f</a></div></body></html>",
        "https://www.njit.edu/careerservices/contact-us": contact,
        "https://www.njit.edu/careerservices/career-fairs": "<html><body><div role='main'>"
            "<h1>Fairs</h1><p>Career fair info.</p></div></body></html>",
    }
    res = C.extract_entry("https://www.njit.edu/careerservices/", lambda u: pages.get(u), budget=20)
    assert len(res.staff) == 16, [s.name for s in res.staff]
    assert any("career-fairs" in p.source_url for p in res.prose)


def test_ingest_writes_people_and_prose(tmp_path):
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    contact = (FIX / "career_contact.html").read_text(encoding="utf-8")
    def fetch(url):
        return contact if url.endswith("/contact-us") else None
    res = C.extract_entry("https://www.njit.edu/careerservices/contact-us", fetch, budget=5)
    summary = C.ingest_career(conn, res)
    conn.commit()
    assert summary["staff"] == 16
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND key LIKE 'crawler/career-development/%'").fetchone()[0]
    assert n == 16

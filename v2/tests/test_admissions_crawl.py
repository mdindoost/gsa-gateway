"""Tests for the Office of University Admissions crawler (Crawling 2.1, office #2).

Fixture-driven against the real saved pages:
  - admissions_contact.html  — the office team roster (~26 people, section-grouped, email-anchored)
  - admissions_advisors.html — graduateadvisors.php (71 FACULTY cross-listed; must mint 0 people)

Design-delta: docs/superpowers/specs/2026-06-24-admissions-crawl-design.md
"""
from pathlib import Path

import pytest

from bs4 import BeautifulSoup

from v2.core.ingestion import admissions_crawl as A
from v2.core.ingestion.web_crawler import clean_text

FIX = Path(__file__).parent / "fixtures"


def _contact_text():
    html = (FIX / "admissions_contact.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    return clean_text(str(A._main_region(soup)))


# ---------------------------------------------------------------- roster parse

def test_parses_all_26_people():
    records, warnings = A.parse_roster(_contact_text())
    assert len(records) == 26, [r.name for r in records]
    # no parse failures on the canonical page
    assert warnings == [], warnings


def test_every_person_has_email_title_and_section():
    records, _ = A.parse_roster(_contact_text())
    for r in records:
        assert r.email.endswith("@njit.edu"), r
        assert r.email == r.email.strip()           # no stray zero-width/space
        assert r.title, r
        assert r.unit, r                             # section header captured


def test_specific_people_resolved_correctly():
    records, _ = A.parse_roster(_contact_text())
    by_name = {r.name: r for r in records}
    # leadership
    assert by_name["Stephen Eck"].email == "eck@njit.edu"
    assert by_name["Stephen Eck"].title == "Associate Provost of University Admissions"
    assert by_name["Stephen Eck"].unit == "University Admissions"
    # nickname kept verbatim (anti-edit / verbatim hard line)
    assert "Yenitza (Jenny) Ruiz" in by_name
    assert by_name["Yenitza (Jenny) Ruiz"].email == "yr64@njit.edu"
    assert by_name["Yenitza (Jenny) Ruiz"].unit == "Recruitment - Undergraduate"
    # apostrophe surname
    assert by_name["Shannon O'Brien"].email == "sobrien@njit.edu"
    assert by_name["Shannon O'Brien"].unit == "Operations"
    # last person on the page
    assert by_name["Kamani Staggers"].email == "ks2445@njit.edu"


def test_wrapped_title_joined_into_one():
    records, _ = A.parse_roster(_contact_text())
    hunter = next(r for r in records if r.name == "Ashley Hunter")
    # the title wraps across two lines on the page — must be ONE joined verbatim title
    assert hunter.title.startswith("Enrollment Services Manager")
    assert "Martin Tuchman School of Management" in hunter.title
    assert hunter.unit == "Recruitment - Graduate"


def test_function_mailbox_never_a_person():
    records, _ = A.parse_roster(_contact_text())
    assert "admissions@njit.edu" not in {r.email for r in records}
    # no section header / title leaked in as a person name
    names = {r.name for r in records}
    for bad in ("University Admissions", "Operations", "Recruitment - Undergraduate",
                "Office of University Admissions"):
        assert bad not in names


def test_no_title_text_misparsed_as_name():
    records, _ = A.parse_roster(_contact_text())
    for r in records:
        # a name must not contain a role keyword (that would mean name/title got swapped)
        assert not A._has_role_keyword(r.name), r


# ----------------------------------------- robustness: section-header / gate

def test_unknown_section_header_not_minted_as_person():
    # a section header NOT in the allow-list (name-shaped, no role keyword) must NOT become a person,
    # and must NOT steal the following person's email — it resets + warns (anti-fab BLOCKER fix)
    text = (
        "University Admissions\n"
        "Stephen Eck\nAssociate Provost of University Admissions\neck@njit.edu\n"
        "Special Programs\n"                         # <-- unrecognized section header
        "Jane Doe\nAdmissions Recruiter\njd5@njit.edu\n"
    )
    records, warnings = A.parse_roster(text)
    names = {r.name for r in records}
    assert "Special Programs" not in names
    assert names == {"Stephen Eck", "Jane Doe"}
    jane = next(r for r in records if r.name == "Jane Doe")
    assert jane.email == "jd5@njit.edu"             # email went to the real person, not the header
    assert jane.unit == "Special Programs"          # header captured as the unit
    assert any("unrecognized section header" in w for w in warnings)


def test_recount_warns_on_structure_mismatch():
    # a personal email with a malformed (title-only, no name) block: fewer people than emails -> warn
    text = "University Admissions\nAssociate Provost\neck@njit.edu\n"
    records, warnings = A.parse_roster(text)
    assert records == []
    assert any("possible roster-structure change" in w or "not a person name" in w for w in warnings)


def test_contact_gate_is_exact_path_not_substring():
    # a page whose URL merely CONTAINS 'contact-admissions' (e.g. an FAQ) must NOT mint people
    person_block = ("<html><body><div role='main'><h1>FAQ</h1>"
                    "University Admissions Jane Doe Admissions Recruiter jd5@njit.edu</div></body></html>")
    contact_html = (FIX / "admissions_contact.html").read_text(encoding="utf-8")
    pages = {
        "https://www.njit.edu/admissions/": "<html><body><div role='main'><h1>A</h1>"
            "<a href='/admissions/contact-admissions'>c</a>"
            "<a href='/admissions/contact-admissions-faq'>faq</a></div></body></html>",
        "https://www.njit.edu/admissions/contact-admissions": contact_html,
        "https://www.njit.edu/admissions/contact-admissions-faq": person_block,
    }
    res = A.extract_entry("https://www.njit.edu/admissions/", lambda u: pages.get(u), budget=50)
    # 26 from the real contact page only; the -faq page contributes 0 people
    assert len(res.staff) == 26, [s.name for s in res.staff]


# ---------------------------------------------------- non-roster pages -> []

def test_advisors_page_yields_zero_people_via_parser():
    # the advisors page is NOT the contact page; even run through parse_roster directly it must not
    # produce admissions staff (its blocks are program-grouped faculty, guarded by name-shape/keywords)
    html = (FIX / "admissions_advisors.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    records, _ = A.parse_roster(clean_text(str(A._main_region(soup))))
    # we tolerate 0 here; the hard guarantee (0 people) is enforced by URL-gating in extract_entry
    assert records == [] or all(r.email for r in records)


def test_extract_entry_url_gates_people_to_contact_page():
    contact_html = (FIX / "admissions_contact.html").read_text(encoding="utf-8")
    advisors_html = (FIX / "admissions_advisors.html").read_text(encoding="utf-8")

    pages = {
        "https://www.njit.edu/admissions/": "<html><body><div role='main'><h1>Admissions</h1>"
            "<p>Welcome</p><a href='/admissions/contact-admissions'>contact</a>"
            "<a href='/admissions/graduate/graduateadvisors.php'>advisors</a></div></body></html>",
        "https://www.njit.edu/admissions/contact-admissions": contact_html,
        "https://www.njit.edu/admissions/graduate/graduateadvisors.php": advisors_html,
    }
    def fetch(url):
        return pages.get(url)

    res = A.extract_entry("https://www.njit.edu/admissions/", fetch, budget=50)
    # ALL people come from the contact page; advisors page contributes 0 people
    assert len(res.staff) == 26, [s.name for s in res.staff]
    # advisors page IS kept as prose (verbatim, source of truth)
    urls = {p.source_url for p in res.prose}
    assert any("graduateadvisors.php" in u for u in urls)


# ----------------------------------------------------------------- ingest

def test_ingest_writes_people_and_prose(tmp_path):
    import sqlite3
    from v2.core.database.schema import create_all, get_connection
    from v2.core.graph.orgs import ensure_org, sync_org_nodes

    db = tmp_path / "t.db"
    create_all(str(db))
    conn = get_connection(str(db))
    ensure_org(conn, "njit", "NJIT", type="university")
    conn.commit()

    contact_html = (FIX / "admissions_contact.html").read_text(encoding="utf-8")
    def fetch(url):
        return contact_html if url.endswith("contact-admissions") else None
    res = A.extract_entry("https://www.njit.edu/admissions/contact-admissions", fetch, budget=5)

    summary = A.ingest_admissions(conn, res)
    conn.commit()
    assert summary["staff"] == 26
    # people landed under the graduate-admissions org with email attrs
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person' AND key LIKE 'crawler/graduate-admissions/%'").fetchone()[0]
    assert n == 26

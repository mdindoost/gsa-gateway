"""classify_candidate — the anti-fabrication boundary (DB-backed).

STRICT (auto-write) only when: verified njit.edu email + strong name match + (unique surname OR
a corroborating dept/interest signal). The headline safety test: two active NJIT 'Wang's + a
verified-njit profile with no corroboration MUST classify 'uncertain', never 'strict'.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.people_editor import set_person_research_areas
from v2.core.ingestion.scholar_discovery import (
    surname_is_unique, classify_candidate,
)


def _ident(name, domain="njit.edu", affiliation="", blocked=False):
    return {"name": name, "verified_email_domain": domain, "affiliation": affiliation, "blocked": blocked}


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    def appoint(key, name, org=cs):
        project_appointment(c, person_key=key, name=name, org_id=org, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    appoint("p/koutis", "Ioannis Koutis")           # unique surname
    appoint("p/jane", "Jane Wang")                  # homonym pair...
    appoint("p/john", "John Wang")
    c.commit()
    yield c, cs
    c.close()


def test_surname_unique_vs_homonym(db):
    conn, _ = db
    assert surname_is_unique(conn, "Ioannis Koutis") is True
    assert surname_is_unique(conn, "Jane Wang") is False


def test_strict_when_unique_surname_njit_verified_and_match(db):
    conn, _ = db
    r = classify_candidate(conn, "p/koutis", "Ioannis Koutis", _ident("Ioannis Koutis"), [])
    assert r["decision"] == "strict" and r["basis"] == "unique_surname"


def test_homonym_njit_verified_no_corroboration_is_uncertain(db):
    # THE headline anti-fabrication test: colliding surname, verified njit, name matches,
    # but nothing corroborates which Wang -> must NOT auto-write.
    conn, _ = db
    r = classify_candidate(conn, "p/jane", "Jane Wang", _ident("Jane Wang", affiliation="Some University"), [])
    assert r["decision"] == "uncertain"


def test_homonym_becomes_strict_with_department_corroboration(db):
    conn, _ = db
    r = classify_candidate(conn, "p/jane", "Jane Wang",
                           _ident("Jane Wang", affiliation="Associate Professor, Computer Science, NJIT"), [])
    assert r["decision"] == "strict" and r["basis"] == "dept_match"


def test_homonym_becomes_strict_with_interest_overlap(db):
    conn, cs = db
    set_person_research_areas(conn, person_key="p/jane", areas=["Graph Machine Learning"], org_id=cs)
    conn.commit()
    r = classify_candidate(conn, "p/jane", "Jane Wang",
                           _ident("Jane Wang", affiliation="Some University"),
                           ["Graph Machine Learning", "Databases"])
    assert r["decision"] == "strict" and r["basis"] == "interest_overlap"


def test_non_njit_verified_domain_is_reject(db):
    conn, _ = db
    r = classify_candidate(conn, "p/koutis", "Ioannis Koutis", _ident("Ioannis Koutis", domain="unh.edu"), [])
    assert r["decision"] == "reject"


def test_no_verified_email_is_uncertain(db):
    conn, _ = db
    r = classify_candidate(conn, "p/koutis", "Ioannis Koutis",
                           _ident("Ioannis Koutis", domain=None, affiliation="NJIT"), [])
    assert r["decision"] == "uncertain"


def test_blocked_page_is_blocked(db):
    conn, _ = db
    r = classify_candidate(conn, "p/koutis", "Ioannis Koutis", _ident(None, blocked=True), [])
    assert r["decision"] == "blocked"


def test_name_mismatch_is_reject(db):
    conn, _ = db
    r = classify_candidate(conn, "p/koutis", "Ioannis Koutis", _ident("Someone Else"), [])
    assert r["decision"] == "reject"

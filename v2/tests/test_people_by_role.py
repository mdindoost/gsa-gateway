from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.entity import people_by_role


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    nce = ensure_org(c, "nce", "Newark College of Engineering", "njit", type="college")
    csla = ensure_org(c, "csla", "College of Science and Liberal Arts", "njit", type="college")
    sync_org_nodes(c)
    def appt(key, name, org, title):
        project_appointment(c, person_key=key, name=name, org_id=org, category="admin",
                            titles=[title], source_section="manual", source="dashboard")
    appt("d/pelesko", "John Pelesko", njit, "Provost and Executive Vice President of Academic Affairs")
    appt("d/dhawan", "Atam Dhawan", njit, "Senior Vice Provost for Research")          # NOT the provost
    appt("d/curko", "Sandy A. Curko", njit, "General Counsel, Senior Vice President of Legal Affairs")
    appt("d/boger", "Marybeth Boger", njit, "Senior Vice President of Student Affairs and Dean of Students")
    appt("d/rodgers", "Shakera Rodgers", njit, "Executive Assistant, Dean of Students and Campus Life")
    appt("d/kam", "Moshe Kam", nce, "Dean, Newark College of Engineering")
    appt("d/gerrard", "Andrew Gerrard", nce, "Professor and Department Chair")
    appt("d/vchair", "Vic Chairman", nce, "Vice Chair")
    appt("d/belfield", "Kevin Belfield", csla, "Dean, College of Science and Liberal Arts")
    c.commit()
    yield c
    c.close()


def _names(rows): return sorted(r[0] for r in rows)


def test_provost_excludes_vice_provost(conn):
    assert _names(people_by_role(conn, "provost")) == ["John Pelesko"]   # not Dhawan (Sr Vice Provost)

def test_dean_of_students_excludes_executive_assistant(conn):
    # compound title segment matches Boger; the Executive Assistant's trailing "Dean of Students"
    # must NOT name her as the dean.
    assert _names(people_by_role(conn, "dean of students")) == ["Marybeth Boger"]

def test_general_counsel_compound_title(conn):
    assert _names(people_by_role(conn, "general counsel")) == ["Sandy A. Curko"]

def test_dean_names_all_across_orgs(conn):
    # "who are the deans" → every dean (name all), org-agnostic: the two college deans AND the
    # Dean of Students (Boger). The Executive Assistant is excluded (support-staff lead).
    got = _names(people_by_role(conn, "dean"))
    assert "Kevin Belfield" in got and "Moshe Kam" in got and "Marybeth Boger" in got
    assert "Shakera Rodgers" not in got

def test_role_scoped_to_one_org(conn):
    nce = conn.execute("SELECT id FROM organizations WHERE slug='nce'").fetchone()[0]
    assert _names(people_by_role(conn, "dean", nce)) == ["Moshe Kam"]

def test_absent_role_is_empty(conn):
    assert people_by_role(conn, "chancellor") == []


def test_department_chair_matches_chair_but_not_vice_chair(conn):
    nce = conn.execute("SELECT id FROM organizations WHERE slug='nce'").fetchone()[0]
    got = _names(people_by_role(conn, "chair", nce))
    assert "Andrew Gerrard" in got          # "Department Chair" matches "chair"
    assert "Vic Chairman" not in got        # "Vice Chair" is a rank modifier, not scope

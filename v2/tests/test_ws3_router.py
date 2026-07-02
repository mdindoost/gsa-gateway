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
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "gsa", "Graduate Student Association", "njit", type="gsa")
    ensure_org(c, "acm", "ACM Student Chapter", "gsa", type="club")
    ensure_org(c, "wics", "Women in Computing Society", "gsa", type="club")
    ensure_org(c, "mtsm", "Martin Tuchman School of Management", "njit", type="college")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.commit()
    yield c
    c.close()


def _skill(conn, q):
    r = route(conn, q)
    return r.skill if r else None


def test_email_routes_contact(conn):
    assert _skill(conn, "Koutis's email") == "contact_of_person"


def test_contact_phrase_routes_contact(conn):
    assert _skill(conn, "how do I contact professor Koutis") == "contact_of_person"


def test_title_routes_title(conn):
    assert _skill(conn, "what is Koutis's position") == "title_of_person"


def test_what_does_x_do_routes_title(conn):
    assert _skill(conn, "what does Koutis do") == "title_of_person"


def test_who_is_still_entity_card(conn):
    assert _skill(conn, "who is Koutis") == "entity_card"


def test_clubs_routes_orgs_by_type(conn):
    r = route(conn, "what clubs are there")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_list_student_orgs_routes_club(conn):
    r = route(conn, "list student organizations")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_how_many_clubs(conn):
    r = route(conn, "how many clubs")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_list_colleges(conn):
    r = route(conn, "list the colleges")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "college"


def test_list_colleges_at_njit_is_unscoped(conn):
    # "at NJIT" resolves the root but must NOT scope colleges to it (blocker: eager parent)
    r = route(conn, "list the colleges at NJIT")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "college"
    assert r.args["parent_org_id"] is None


def test_departments_in_ywcc_stays_org_departments(conn):
    assert _skill(conn, "departments in Ying Wu College of Computing") == "org_departments"


def test_faculty_in_dept_not_orgs_by_type(conn):
    # head-noun is faculty; 'department' merely names the org — must NOT become orgs_by_type
    assert _skill(conn, "list faculty in Computer Science department") != "orgs_by_type"


def test_who_is_chair_stays_people_by_role(conn):
    assert _skill(conn, "who is the chair of Computer Science") == "people_by_role"


# ── over-match negatives (review BLOCKER: bare what/which + singular type noun must NOT fire B3) ──
def test_which_college_is_x_in_not_b3(conn):
    assert _skill(conn, "which college is Computer Science in") != "orgs_by_type"


def test_what_college_should_i_apply_not_b3(conn):
    assert _skill(conn, "what college should I apply to") != "orgs_by_type"


def test_which_department_is_koutis_in_not_b3(conn):
    # must not dump all departments (review BLOCKER: unscoped-dept cannibalization)
    assert _skill(conn, "which department is Koutis in") != "orgs_by_type"


def test_office_hours_not_contact(conn):
    # "office hours" is a schedule ask, not a contact field (review MINOR)
    assert _skill(conn, "Koutis's office hours") != "contact_of_person"


# ── pronoun hardneg (review MAJOR: bare pronoun stays out of KG, no wrong-person) ──
def test_pronoun_position_not_kg(conn):
    assert route(conn, "what is his position") is None


def test_pronoun_contact_not_kg(conn):
    assert route(conn, "who do I contact about this") is None

import sqlite3
import pytest
from facultyfolio import db, config


def test_koutis_dict():
    f = db.get_faculty(33)
    assert f["name"] == "Ioannis Koutis"                 # normalized from "Koutis, Ioannis" (B1)
    assert f["slug"] == "ikoutis"
    assert f["title"] == "Associate Professor"
    assert f["home_dept"] == "Computer Science"
    assert f["college"] == "Ying Wu College of Computing"
    assert f["joint_dept"] is None
    assert "4105" in f["office"]
    assert f["email"] == "ioannis.koutis@njit.edu"
    assert set(f["profiles"]) >= {"scholar", "linkedin", "github", "website"}
    assert len(f["areas"]) == 5
    assert len(set(a.lower().replace(" ", "") for a in f["areas"])) == 5   # deduped
    assert f["scholar"]["citations"] >= 2791          # grows over time (Scholar refreshes); don't re-pin
    assert "Carnegie Mellon" in f["education_raw"]
    assert "CS 375" in f["teaching_raw"] or "CS375" in f["teaching_raw"]


def test_readonly_connection_rejects_write():
    conn = db.connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE _ff_probe(x)")


def test_trust_boundary_only_crawler_prose():
    f = db.get_faculty(33)
    assert "about" not in f["_prose_types"]          # never LLM bios
    assert set(f["_prose_types"]) <= {"education", "teaching", "profile", "research_statement"}


def test_get_faculty_research_statement_raw():
    # mx6's NJIT research interest is a prose sentence (no chips) -> stored as research_statement
    rs = db.get_faculty("mx6")["research_statement_raw"]
    assert "Research statement of" in rs and "Machine learning theory" in rs


def test_cs_faculty_slugs():
    slugs = db.cs_faculty_slugs()
    assert "ikoutis" in slugs and "oria" in slugs and "km982" in slugs
    assert len(slugs) == 57


def test_faculty_slugs_per_org():
    assert len(db.faculty_slugs(16)) == 57                    # Computer Science
    assert len(db.faculty_slugs(73)) == 21                    # Data Science
    assert len(db.faculty_slugs(100)) == 41                   # Informatics
    assert db.faculty_slugs(config.CS_ORG_ID) == db.cs_faculty_slugs()


def test_org_node_by_slug():
    assert db.org_node_by_slug("ywcc") == 299
    assert db.org_node_by_slug("computer-science") == 16
    assert db.org_node_by_slug("no-such-org") is None


def test_dept_orgs_of_college_discovers_ywcc_depts():
    depts = db.dept_orgs_of_college(299)
    slugs = [d["slug"] for d in depts]
    # only faculty>0 depts, sorted by slug; College Administration (0 faculty) excluded
    assert slugs == ["computer-science", "data-science", "informatics"]
    assert "college-administration" not in slugs
    by_slug = {d["slug"]: d for d in depts}
    assert by_slug["data-science"]["faculty"] == 21
    assert by_slug["data-science"]["node_id"] == 73
    assert by_slug["computer-science"]["name"] == "Computer Science"


def test_college_name_expands_acronym():
    assert db.college_name(299) == "Ying Wu College of Computing"


def test_get_faculty_home_dept_segment():
    assert db.get_faculty(33)["home_dept_segment"] == "computer-science"   # Koutis

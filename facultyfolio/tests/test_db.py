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
    assert f["scholar"]["citations"] == 2791
    assert "Carnegie Mellon" in f["education_raw"]
    assert "CS 375" in f["teaching_raw"] or "CS375" in f["teaching_raw"]


def test_readonly_connection_rejects_write():
    conn = db.connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE _ff_probe(x)")


def test_trust_boundary_only_crawler_prose():
    f = db.get_faculty(33)
    assert "about" not in f["_prose_types"]          # never LLM bios
    assert set(f["_prose_types"]) <= {"education", "teaching", "profile"}


def test_cs_faculty_slugs():
    slugs = db.cs_faculty_slugs()
    assert "ikoutis" in slugs and "oria" in slugs and "km982" in slugs
    assert len(slugs) == 57

from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.skills import top_people_by_metric


def _appoint(conn, key, name, org_id, category="faculty", title="Professor"):
    project_appointment(conn, person_key=key, name=name, org_id=org_id, category=category,
                        titles=[title], source_section="manual", source="dashboard")


def _set_scholar(conn, key, **metrics):
    conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?",
                 (json.dumps({"profiles": {"scholar": metrics}}), key))


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    ds = ensure_org(c, "ds", "Data Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    c.commit()
    yield c, {"njit": 1, "ywcc": ywcc, "cs": cs, "ds": ds}
    c.close()


def test_ranks_desc_and_counts(db):
    conn, org = db
    _appoint(conn, "p/koutis", "Ioannis Koutis", org["cs"])
    _appoint(conn, "p/low", "Low Cite", org["cs"])
    _appoint(conn, "p/none", "No Metrics", org["cs"])
    _set_scholar(conn, "p/koutis", citations=2774)
    _set_scholar(conn, "p/low", citations=100)
    conn.commit()
    r = top_people_by_metric(conn, org["cs"], "scholar", "citations")
    assert r["ranked"] == [("Ioannis Koutis", 2774), ("Low Cite", 100)]
    assert r["with_metric"] == 2
    assert r["total_in_org"] == 3  # includes the person with no metrics


def test_subtree_scope_college_includes_department_people(db):
    conn, org = db
    _appoint(conn, "p/cs1", "CS One", org["cs"])
    _appoint(conn, "p/ds1", "DS One", org["ds"])
    _set_scholar(conn, "p/cs1", citations=50)
    _set_scholar(conn, "p/ds1", citations=75)
    conn.commit()
    r = top_people_by_metric(conn, org["ywcc"], "scholar", "citations")
    assert r["ranked"] == [("DS One", 75), ("CS One", 50)]
    assert r["total_in_org"] == 2


def test_person_with_two_roles_counted_once(db):
    conn, org = db
    # same person appointed in two orgs within the subtree -> two has_role edges.
    _appoint(conn, "p/dual", "Dual Role", org["cs"], category="faculty")
    _appoint(conn, "p/dual", "Dual Role", org["ds"], category="admin", title="Director")
    _set_scholar(conn, "p/dual", citations=500)
    conn.commit()
    r = top_people_by_metric(conn, org["ywcc"], "scholar", "citations")
    assert r["ranked"] == [("Dual Role", 500)]   # once, not twice
    assert r["with_metric"] == 1
    assert r["total_in_org"] == 1


def test_empty_when_nobody_has_metric(db):
    conn, org = db
    _appoint(conn, "p/a", "A", org["cs"])
    _appoint(conn, "p/b", "B", org["cs"])
    conn.commit()
    r = top_people_by_metric(conn, org["cs"], "scholar", "citations")
    assert r["ranked"] == []
    assert r["with_metric"] == 0
    assert r["total_in_org"] == 2


def test_tie_returns_both_sorted_by_name(db):
    conn, org = db
    _appoint(conn, "p/x", "Xavier Tie", org["cs"])
    _appoint(conn, "p/a", "Aaron Tie", org["cs"])
    _set_scholar(conn, "p/x", citations=2774)
    _set_scholar(conn, "p/a", citations=2774)
    conn.commit()
    r = top_people_by_metric(conn, org["cs"], "scholar", "citations")
    assert r["ranked"] == [("Aaron Tie", 2774), ("Xavier Tie", 2774)]

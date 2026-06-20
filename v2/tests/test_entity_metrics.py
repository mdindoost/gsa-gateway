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
from v2.core.retrieval.entity import metric_of_person


def _person(conn, key, name, org_id, profiles=None):
    project_appointment(conn, person_key=key, name=name, org_id=org_id, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    if profiles is not None:
        conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?",
                     (json.dumps({"profiles": profiles}), key))
    conn.commit()


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="njit", type="department")
    sync_org_nodes(c)
    c.commit()
    yield c, cs
    c.close()


def test_metric_of_person_single_asked_metric(db):
    conn, cs = db
    _person(conn, "p/koutis", "Ioannis Koutis", cs,
            {"scholar": {"citations": 2774, "h_index": 26, "i10_index": 35, "updated_at": "2026-06"}})
    r = metric_of_person(conn, "p/koutis", "scholar", "citations")
    assert r["name"] == "Ioannis Koutis"
    assert r["found"] == {"citations": 2774}
    assert r["all"] == {"citations": 2774, "h_index": 26, "i10_index": 35}
    assert r["updated_at"] == "2026-06"


def test_metric_of_person_all_metrics_when_no_key(db):
    conn, cs = db
    _person(conn, "p/koutis", "Ioannis Koutis", cs,
            {"scholar": {"citations": 2774, "h_index": 26, "i10_index": 35}})
    r = metric_of_person(conn, "p/koutis", "scholar", None)
    assert r["found"] == {"citations": 2774, "h_index": 26, "i10_index": 35}


def test_metric_of_person_partial_miss_keeps_present_in_all(db):
    conn, cs = db
    # has citations, asked for h_index -> found empty, but `all` still offers citations.
    _person(conn, "p/x", "Pat X", cs, {"scholar": {"citations": 100}})
    r = metric_of_person(conn, "p/x", "scholar", "h_index")
    assert r["found"] == {}
    assert r["all"] == {"citations": 100}


def test_metric_of_person_honest_empty(db):
    conn, cs = db
    _person(conn, "p/none", "No Metrics", cs, {"scholar": {"url": "x"}})
    r = metric_of_person(conn, "p/none", "scholar", "citations")
    assert r["found"] == {}
    assert r["all"] == {}


def test_metric_of_person_unknown_person(db):
    conn, cs = db
    r = metric_of_person(conn, "p/ghost", "scholar", "citations")
    assert r["found"] == {}
    assert r["all"] == {}

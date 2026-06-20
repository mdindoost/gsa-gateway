"""scholar_scope_list — the dashboard scope dropdown data (one pass).

'All faculty' + each college + each department, with the count of distinct people in that
subtree who carry a Scholar URL. A college's count includes its departments' people, deduped.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.scholar import scholar_scope_list


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    ds = ensure_org(c, "ds", "Data Science", parent_slug="ywcc", type="department")
    nce = ensure_org(c, "nce", "NCE", parent_slug="njit", type="college")
    sync_org_nodes(c)
    def appoint(key, name, org):
        project_appointment(c, person_key=key, name=name, org_id=org, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    appoint("p/cs1", "CS One", cs); appoint("p/cs2", "CS Two", cs)
    appoint("p/ds1", "DS One", ds); appoint("p/nce1", "NCE One", nce)
    appoint("p/noschol", "No Scholar", cs)  # no scholar url -> not counted
    for k in ("p/cs1", "p/cs2", "p/ds1", "p/nce1"):
        c.execute("UPDATE nodes SET attrs=? WHERE key=?",
                  (json.dumps({"profiles": {"scholar": {"url": f"https://s/{k}"}}}), k))
    c.commit()
    yield c
    c.close()


def _by_slug(rows):
    return {r["slug"]: r for r in rows}


def test_all_entry_counts_everyone_with_scholar(db):
    rows = scholar_scope_list(db)
    by = _by_slug(rows)
    assert by[""]["label"].lower().startswith("all")
    assert by[""]["eligible"] == 4          # cs1, cs2, ds1, nce1 (noschol excluded)


def test_college_count_rolls_up_departments(db):
    by = _by_slug(scholar_scope_list(db))
    assert by["ywcc"]["eligible"] == 3      # cs1 + cs2 + ds1
    assert by["ywcc"]["type"] == "college"
    assert by["nce"]["eligible"] == 1


def test_department_counts_are_just_that_department(db):
    by = _by_slug(scholar_scope_list(db))
    assert by["cs"]["eligible"] == 2 and by["cs"]["type"] == "department"
    assert by["ds"]["eligible"] == 1


def test_person_in_two_subtree_orgs_counted_once_in_college(db):
    # appoint cs1 also into ds -> still 3 distinct in ywcc, not 4
    project_appointment(db, person_key="p/cs1", name="CS One",
                        org_id=[r[0] for r in db.execute("SELECT id FROM organizations WHERE slug='ds'")][0],
                        category="admin", titles=["Director"], source_section="manual", source="dashboard")
    db.commit()
    by = _by_slug(scholar_scope_list(db))
    assert by["ywcc"]["eligible"] == 3

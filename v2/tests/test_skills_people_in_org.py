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
from v2.core.retrieval.skills import people_in_org, officers_in_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_people_in_org_returns_all_role_types(conn):
    gs = ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/graduate-studies/sotirios-ziavras",
                        name="Sotirios Ziavras", org_id=gs, category="admin",
                        titles=["Dean of Graduate Studies"], source_section="manual", source="dashboard")
    project_appointment(conn, person_key="dashboard/graduate-studies/ester-flaim",
                        name="Ester Flaim", org_id=gs, category="staff",
                        titles=["Assistant Director"], source_section="manual", source="dashboard")
    nt = [(n, t) for n, t, _ in people_in_org(conn, gs)]
    assert ("Sotirios Ziavras", "Dean of Graduate Studies") in nt
    assert ("Ester Flaim", "Assistant Director") in nt
    # officers_in_org now also surfaces 'admin' leadership (President/Provost/Dean) — so the Dean
    # appears, but a plain 'staff' member does not.
    offs = [(n, t) for n, t, _ in officers_in_org(conn, gs)]
    assert ("Sotirios Ziavras", "Dean of Graduate Studies") in offs
    assert ("Ester Flaim", "Assistant Director") not in offs


def test_people_in_org_excludes_inactive(conn):
    gs = ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/graduate-studies/x", name="X",
                        org_id=gs, category="advisor", titles=["Advisor"],
                        source_section="manual", source="dashboard")
    conn.execute("UPDATE edges SET is_active=0 WHERE type='has_role'")
    assert people_in_org(conn, gs) == []

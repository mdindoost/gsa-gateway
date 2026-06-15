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
from v2.core.retrieval.skills import officers_in_org, resolve_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_officers_in_org_returns_name_and_title(conn):
    phd = ensure_org(conn, "phd-club", "PhD Club", parent_slug="gsa", type="unit")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/gsa/mohith-oduru", name="Mohith Oduru",
                        org_id=2, category="officer", titles=["VP Finances"],
                        source_section="E-Board", source="dashboard")
    project_appointment(conn, person_key="dashboard/gsa/fernando", name="Fernando Vera Buschmann",
                        org_id=2, category="officer", titles=["GSA President"],
                        source_section="E-Board", source="dashboard")
    project_appointment(conn, person_key="dashboard/phd-club/ana", name="Ana Lee",
                        org_id=phd, category="officer", titles=["President"],
                        source_section="RGO", source="dashboard")
    gsa = resolve_org(conn, "gsa")
    officers = officers_in_org(conn, gsa)
    assert ("Fernando Vera Buschmann", "GSA President") in officers
    assert ("Mohith Oduru", "VP Finances") in officers
    assert all(name != "Ana Lee" for name, _ in officers)
    assert ("Ana Lee", "President") in officers_in_org(conn, phd)


def test_officers_in_org_ignores_inactive_and_other_categories(conn):
    sync_org_nodes(conn)
    pid = project_appointment(conn, person_key="dashboard/gsa/x", name="Faculty X",
                              org_id=2, category="faculty", titles=["Professor"],
                              source_section="E-Board", source="dashboard")
    assert officers_in_org(conn, 2) == []

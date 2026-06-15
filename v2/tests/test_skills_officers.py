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


def test_officers_in_org_returns_name_title_email(conn):
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
    nt = [(n, t) for n, t, _ in officers_in_org(conn, gsa)]
    assert ("Fernando Vera Buschmann", "GSA President") in nt
    assert ("Mohith Oduru", "VP Finances") in nt
    assert all(name != "Ana Lee" for name, _ in nt)
    assert ("Ana Lee", "President") in [(n, t) for n, t, _ in officers_in_org(conn, phd)]
    # email is surfaced from the Person node attrs
    conn.execute("UPDATE nodes SET attrs='{\"email\":\"gsa-vpf@njit.edu\"}' "
                 "WHERE key='dashboard/gsa/mohith-oduru'")
    assert any(n == "Mohith Oduru" and e == "gsa-vpf@njit.edu"
               for n, _t, e in officers_in_org(conn, gsa))


def test_officers_in_org_ignores_inactive_and_other_categories(conn):
    sync_org_nodes(conn)
    # 'faculty' is not an officer/deprep role -> excluded
    project_appointment(conn, person_key="dashboard/gsa/x", name="Faculty X",
                        org_id=2, category="faculty", titles=["Professor"],
                        source_section="E-Board", source="dashboard")
    # a DepRep (category 'deprep') IS included
    project_appointment(conn, person_key="dashboard/gsa/rep", name="Dana Rep",
                        org_id=2, category="deprep", titles=["Dept Representative"],
                        source_section="DepRep", source="dashboard")
    assert ("Dana Rep", "Dept Representative") in [(n, t) for n, t, _ in officers_in_org(conn, 2)]
    # deactivating the deprep's appointment removes them (exercises e.is_active filter)
    conn.execute("UPDATE edges SET is_active=0 WHERE category='deprep'")
    assert all(r[0] != "Dana Rep" for r in officers_in_org(conn, 2))
    # the faculty person never appears
    assert all(r[0] != "Faculty X" for r in officers_in_org(conn, 2))

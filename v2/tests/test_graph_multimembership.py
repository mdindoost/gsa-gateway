from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import project_appointment
from v2.core.graph.store import upsert_node


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(4,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(20,4,'College Administration','college-administration','admin-unit')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(5,4,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_two_paths_accumulate_not_overwrite(conn):
    # Wang reached via College Administration (admin) then via CS (faculty)
    project_appointment(conn, person_key="p/gwang", name="Guiling Wang", org_id=20,
                        category="admin", titles=["Associate Dean of Research"],
                        source_section="Associate Deans")
    project_appointment(conn, person_key="p/gwang", name="Guiling Wang", org_id=5,
                        category="faculty", titles=["Distinguished Professor"],
                        source_section="Professors")
    pid = conn.execute("SELECT id FROM nodes WHERE key='p/gwang'").fetchone()[0]
    rows = conn.execute(
        "SELECT category FROM edges WHERE src_id=? AND type='has_role' AND is_active=1 "
        "ORDER BY category", (pid,)).fetchall()
    assert [r[0] for r in rows] == ["admin", "faculty"]   # BOTH appointments survive


def test_appointment_preserves_existing_person_attrs(conn):
    # a profile pass set contact attrs; a later listing appointment must not wipe them
    upsert_node(conn, type="Person", key="p/x", name="X", attrs={"email": "x@njit.edu"})
    project_appointment(conn, person_key="p/x", name="X", org_id=5, category="faculty",
                        titles=["Professor"], source_section="Professors")
    attrs = conn.execute("SELECT attrs FROM nodes WHERE key='p/x'").fetchone()[0]
    assert "x@njit.edu" in attrs

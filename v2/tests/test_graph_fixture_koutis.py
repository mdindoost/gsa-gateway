from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.njit_adapter import parse_entity
from v2.core.ingestion.reconcile import reconcile_entity

FIXTURE = Path(__file__).parent / "fixtures" / "koutis_profile.html"


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_saved_cs_profile_populates_graph_consistently(conn):
    html = FIXTURE.read_text(encoding="utf-8")
    rec = parse_entity("https://people.njit.edu/profile/ikoutis", html)
    reconcile_entity(conn, 5, rec.entity_id, decompose(rec), rec=rec)

    person = conn.execute(
        "SELECT id,name,attrs FROM nodes WHERE type='Person' AND key=?",
        (rec.entity_id,)).fetchone()
    assert person is not None and person["name"] == "Ioannis Koutis"
    assert "ioannis.koutis@njit.edu" in person["attrs"]

    role = conn.execute(
        "SELECT category FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND o.key='computer-science'",
        (person["id"],)).fetchone()
    assert role is not None and role["category"] == "faculty"

    rs = conn.execute(
        "SELECT area_source FROM edges WHERE src_id=? AND type='researches' AND is_active=1",
        (person["id"],)).fetchall()
    assert len(rs) >= 1 and all(r["area_source"] == "structured" for r in rs)

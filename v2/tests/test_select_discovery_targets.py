"""select_discovery_targets — faculty in an org subtree WITHOUT a Scholar URL (discovery's set)."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.scholar_discovery import select_discovery_targets


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    nce = ensure_org(c, "nce", "NCE", parent_slug="njit", type="college")
    sync_org_nodes(c)
    def appoint(key, name, org, category="faculty"):
        project_appointment(c, person_key=key, name=name, org_id=org, category=category,
                            titles=["Professor"], source_section="manual", source="dashboard")
    appoint("p/has", "Has Scholar", cs)
    c.execute("UPDATE nodes SET attrs=? WHERE key='p/has'",
              (json.dumps({"profiles": {"scholar": {"url": "https://s/x"}}}),))
    appoint("p/needs", "Needs Scholar", cs)              # faculty, no url -> target
    appoint("p/staff", "Some Staff", cs, category="staff")  # not faculty -> excluded
    appoint("p/nce", "NCE Person", nce)                  # out of ywcc scope
    c.commit()
    yield c
    c.close()


def test_targets_faculty_without_url_in_scope(db):
    got = {k for k, _ in select_discovery_targets(db, org_scope="ywcc")}
    assert got == {"p/needs"}


def test_no_scope_spans_all(db):
    got = {k for k, _ in select_discovery_targets(db)}
    assert "p/needs" in got and "p/nce" in got
    assert "p/has" not in got and "p/staff" not in got


def test_limit_caps(db):
    assert len(select_discovery_targets(db, limit=0)) == 0

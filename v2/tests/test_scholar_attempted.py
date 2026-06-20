"""B1 termination fix: mark_attempted + select_discovery_targets(skip_attempted)."""
from __future__ import annotations
import datetime, json, sys
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.scholar_discovery import mark_attempted, select_discovery_targets


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="p/needs", name="Needs Scholar", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    c.commit(); yield c; c.close()


def _keys(c, **kw): return {k for k, _ in select_discovery_targets(c, **kw)}


def test_mark_attempted_writes_marker_no_url(db):
    mark_attempted(db, "p/needs", "skip", "2026-06-20"); db.commit()
    sch = json.loads(db.execute("SELECT attrs FROM nodes WHERE key='p/needs'").fetchone()[0])["profiles"]["scholar"]
    assert sch["discovery_attempted"] == {"date": "2026-06-20", "decision": "skip"}
    assert "url" not in sch


def test_select_excludes_attempted(db):
    assert "p/needs" in _keys(db)
    mark_attempted(db, "p/needs", "skip", "2026-06-20"); db.commit()
    assert "p/needs" not in _keys(db)                       # excluded by default
    assert "p/needs" in _keys(db, skip_attempted=False)     # opt-out includes it


def test_retry_after_days_reopens_stale_attempt(db):
    mark_attempted(db, "p/needs", "uncertain", "2026-05-01"); db.commit()
    today = datetime.date(2026, 6, 20)                       # ~50 days later
    assert "p/needs" in _keys(db, retry_after_days=30, today=today)    # stale -> retry
    assert "p/needs" not in _keys(db, retry_after_days=90, today=today)  # still fresh -> excluded

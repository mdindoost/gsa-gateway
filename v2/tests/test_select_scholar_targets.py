"""select_scholar_targets — which people a scoped Scholar refresh should fetch.

Read-only: people who carry a Scholar URL, optionally restricted to an org subtree (a college
includes its departments) and optionally only those whose scholar.updated_at is older than N
days. Returns DISTINCT person keys (a person with two in-scope roles appears once).
"""
from __future__ import annotations
import datetime
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
from v2.core.ingestion.scholar import select_scholar_targets


def _appoint(conn, key, name, org_id, category="faculty", title="Professor"):
    project_appointment(conn, person_key=key, name=name, org_id=org_id, category=category,
                        titles=[title], source_section="manual", source="dashboard")


def _scholar(conn, key, *, url="https://scholar.google.com/x", updated_at=None):
    sch = {"url": url}
    if updated_at:
        sch["updated_at"] = updated_at
    conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?",
                 (json.dumps({"profiles": {"scholar": sch}}), key))


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    ds = ensure_org(c, "ds", "Data Science", parent_slug="ywcc", type="department")
    nce = ensure_org(c, "nce", "Newark College of Engineering", parent_slug="njit", type="college")
    sync_org_nodes(c)
    c.commit()
    yield c, {"njit": 1, "ywcc": ywcc, "cs": cs, "ds": ds, "nce": nce}
    c.close()


def test_no_scope_returns_all_scholar_people(db):
    conn, org = db
    _appoint(conn, "p/cs1", "CS One", org["cs"]); _scholar(conn, "p/cs1")
    _appoint(conn, "p/nce1", "NCE One", org["nce"]); _scholar(conn, "p/nce1")
    _appoint(conn, "p/noschol", "No Scholar", org["cs"])  # no scholar url
    conn.commit()
    assert set(select_scholar_targets(conn)) == {"p/cs1", "p/nce1"}


def test_college_scope_includes_department_people(db):
    conn, org = db
    _appoint(conn, "p/cs1", "CS One", org["cs"]); _scholar(conn, "p/cs1")
    _appoint(conn, "p/ds1", "DS One", org["ds"]); _scholar(conn, "p/ds1")
    _appoint(conn, "p/nce1", "NCE One", org["nce"]); _scholar(conn, "p/nce1")
    conn.commit()
    assert set(select_scholar_targets(conn, org_scope="ywcc")) == {"p/cs1", "p/ds1"}


def test_department_scope_is_just_that_department(db):
    conn, org = db
    _appoint(conn, "p/cs1", "CS One", org["cs"]); _scholar(conn, "p/cs1")
    _appoint(conn, "p/ds1", "DS One", org["ds"]); _scholar(conn, "p/ds1")
    conn.commit()
    assert set(select_scholar_targets(conn, org_scope="cs")) == {"p/cs1"}


def test_person_with_two_in_scope_roles_listed_once(db):
    conn, org = db
    _appoint(conn, "p/dual", "Dual Role", org["cs"])
    _appoint(conn, "p/dual", "Dual Role", org["ds"], category="admin", title="Director")
    _scholar(conn, "p/dual")
    conn.commit()
    out = select_scholar_targets(conn, org_scope="ywcc")
    assert out.count("p/dual") == 1


def test_unknown_scope_slug_returns_empty(db):
    conn, _ = db
    assert select_scholar_targets(conn, org_scope="does-not-exist") == []


def test_staleness_excludes_recent_includes_old_and_never(db):
    conn, org = db
    today = datetime.date(2026, 6, 20)
    _appoint(conn, "p/fresh", "Fresh", org["cs"]); _scholar(conn, "p/fresh", updated_at="2026-06-20")
    _appoint(conn, "p/old", "Old", org["cs"]); _scholar(conn, "p/old", updated_at="2026-01-01")
    _appoint(conn, "p/never", "Never", org["cs"]); _scholar(conn, "p/never")  # no updated_at
    conn.commit()
    out = set(select_scholar_targets(conn, older_than_days=30, today=today))
    assert "p/old" in out and "p/never" in out
    assert "p/fresh" not in out


def test_legacy_year_month_parsed_as_month_start(db):
    conn, org = db
    today = datetime.date(2026, 6, 20)
    # "2026-06" == month start 2026-06-01 -> 19 days old -> NOT older than 30
    _appoint(conn, "p/thismonth", "This Month", org["cs"]); _scholar(conn, "p/thismonth", updated_at="2026-06")
    # "2026-04" -> month start 2026-04-01 -> ~80 days -> older than 30
    _appoint(conn, "p/twomonths", "Two Months", org["cs"]); _scholar(conn, "p/twomonths", updated_at="2026-04")
    conn.commit()
    out = set(select_scholar_targets(conn, older_than_days=30, today=today))
    assert "p/twomonths" in out and "p/thismonth" not in out

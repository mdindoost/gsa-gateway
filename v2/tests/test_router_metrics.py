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
from v2.core.retrieval.router import route


def _appoint(conn, key, name, org_id):
    project_appointment(conn, person_key=key, name=name, org_id=org_id, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")


def _set_scholar(conn, key, **m):
    conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?",
                 (json.dumps({"profiles": {"scholar": m}}), key))


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    _appoint(c, "p/koutis", "Ioannis Koutis", cs)
    _appoint(c, "p/weiwang", "Wei Wang", cs)
    _appoint(c, "p/guiwang", "Guiling Wang", cs)
    _set_scholar(c, "p/koutis", citations=2774, h_index=26, i10_index=35)
    c.commit()
    yield c
    c.close()


def test_single_person_metric_by_surname(conn):
    r = route(conn, "koutis citations")
    assert r.skill == "metric_of_person"
    assert r.args["entity_id"] == "p/koutis"
    assert r.args["field_key"] == "scholar"
    assert r.args["metric_key"] == "citations"


def test_org_ranking_most_cited(conn):
    r = route(conn, "who has the most citations in cs")
    assert r.skill == "top_people_by_metric"
    assert r.args["metric_key"] == "citations"
    assert r.args["n"] == 1


def test_org_ranking_top_n_h_index(conn):
    r = route(conn, "top 5 by h-index in ywcc")
    assert r.skill == "top_people_by_metric"
    assert r.args["metric_key"] == "h_index"
    assert r.args["n"] == 5


def test_university_wide_ranking(conn):
    r = route(conn, "who is the most cited at njit")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1


def test_ambiguous_surname_disambiguates(conn):
    r = route(conn, "wang citations")
    assert r.skill == "person_disambig"
    assert len(r.args["candidates"]) == 2


def test_citation_policy_is_not_a_metric_route(conn):
    # metric-ish word but no person, no org+rank -> fall through to RAG (None).
    assert route(conn, "citation policy") is None
    assert route(conn, "how do I cite a paper") is None


def test_most_cited_research_area_routes_to_area_skill_not_metric(conn):
    # precedence: the area branch wins; this must NOT become a metric ranking.
    r = route(conn, "most cited research area in cs")
    assert r is None or r.skill != "top_people_by_metric"


def test_bare_metric_word_falls_through(conn):
    assert route(conn, "citations") is None

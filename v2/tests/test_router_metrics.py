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


# ── Bug A: bare/university-wide metric ranking (no org named) → default to NJIT root ──
def test_most_cited_professor_no_org_defaults_to_root(conn):
    r = route(conn, "who is the most cited professor")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1
    assert r.args["metric_key"] == "citations"
    assert r.args["n"] == 1
    assert r.args.get("org_defaulted") is True


def test_highest_h_index_professor_no_org_defaults_to_root(conn):
    r = route(conn, "highest h-index professor")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1
    assert r.args["metric_key"] == "h_index"
    assert r.args.get("org_defaulted") is True


def test_top_5_most_cited_faculty_no_org_defaults_to_root(conn):
    r = route(conn, "top 5 most cited faculty")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1
    assert r.args["n"] == 5
    assert r.args.get("org_defaulted") is True


def test_who_is_most_cited_person_intent_satisfies_gate(conn):
    # no "professor"/"faculty" word, but "who is" (_PERSON_INTENT) is the cue
    r = route(conn, "who is the most cited")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1
    assert r.args.get("org_defaulted") is True


def test_explicit_org_ranking_is_not_flagged_defaulted(conn):
    r = route(conn, "who is the most cited at njit")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == 1
    assert r.args.get("org_defaulted") in (False, None)   # org explicitly named → no nudge


def test_dept_ranking_stays_scoped_not_root(conn):
    r = route(conn, "most cited professor in cs")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] != 1                          # CS, not the root


# ── Bug A scope gate: metric+rank+no-org WITHOUT a person/faculty cue → RAG (None) ──
def test_most_cited_paper_no_person_cue_falls_through(conn):
    assert route(conn, "most cited paper") is None


def test_top_citation_award_no_person_cue_falls_through(conn):
    assert route(conn, "top citation award") is None


# ── Bug B (Option 3): descending-metric + person cue → deterministic decline ──
def test_least_cited_professor_at_njit_declines(conn):
    r = route(conn, "least cited professor at njit")
    assert r.skill == "metric_descending_unsupported"
    assert r.args["metric_key"] == "citations"


def test_least_cited_professor_no_org_declines(conn):
    r = route(conn, "least cited professor")
    assert r.skill == "metric_descending_unsupported"


def test_fewest_citations_professor_declines(conn):
    r = route(conn, "professor with the fewest citations at njit")
    assert r.skill == "metric_descending_unsupported"


def test_lowest_h_index_professor_declines(conn):
    r = route(conn, "lowest h-index professor at njit")
    assert r.skill == "metric_descending_unsupported"
    assert r.args["metric_key"] == "h_index"


# ── Bug B gate: descending + metric WITHOUT a person/faculty cue → RAG (None) ──
def test_fewest_citations_to_graduate_is_not_a_decline(conn):
    assert route(conn, "fewest citations needed to graduate") is None


def test_papers_with_fewest_citations_is_not_a_decline(conn):
    assert route(conn, "papers with the fewest citations") is None


# ── Task 4: thread org/n into the decline route so a resume can scope top_people_by_metric ──
def test_metric_descending_route_carries_org(conn):
    r = route(conn, "least cited professor in ywcc")
    assert r is not None and r.skill == "metric_descending_unsupported"
    assert r.args.get("org_id") is not None          # ywcc resolved + threaded
    assert "n" in r.args
    assert r.args.get("org_defaulted") is False


def test_metric_descending_no_org_defaults_root(conn):
    r = route(conn, "least cited professor")
    assert r is not None and r.skill == "metric_descending_unsupported"
    assert r.args.get("org_id") is not None          # defaulted to root (NJIT id=1)
    assert r.args.get("org_defaulted") is True


def test_metric_descending_no_root_org_no_false_default():
    # No organizations at all → no root to default to. The decline may still fire (it needs no org),
    # but it must NEVER claim org_defaulted=True while org_id is None (contradictory; a later task
    # trusts org_defaulted=True to imply a usable org_id).
    c = create_all(":memory:")
    r = route(c, "least cited professor")
    c.close()
    if r is not None and r.skill == "metric_descending_unsupported":
        assert not (r.args.get("org_defaulted") and r.args.get("org_id") is None)


# ── Task 7.5: "who has the lowest/most <metric> in <org>" is a person-metric cue too ──
# ("who has" was missing from _PERSON_INTENT, so these fell through to None and lost org_id).
def test_who_has_lowest_metric_in_org_routes_deterministically(conn):
    # the literal reported repro — must now route to the decline WITH org threaded
    r = route(conn, "who has the lowest citation in ywcc")
    assert r is not None and r.skill == "metric_descending_unsupported"
    assert r.args.get("org_id") is not None
    assert r.args.get("org_defaulted") is False


def test_who_has_most_metric_in_org_still_ranks(conn):
    r = route(conn, "who has the most citations in ywcc")
    assert r is not None and r.skill == "top_people_by_metric"


def test_who_has_non_metric_unaffected(conn):
    # blast-radius guard: no metric word -> metric block skipped -> unchanged (None)
    assert route(conn, "who has office hours") is None
    assert route(conn, "who has a phd") is None

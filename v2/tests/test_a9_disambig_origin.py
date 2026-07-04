"""TDD — A9: person_disambig resumes the ORIGINALLY-asked skill.

Before: every disambig option resumed as entity_card (a bio card), dropping the asked question — so
"Wang's h-index" → pick a Wang → bio card, not the h-index. After: the producer tags the disambig Route
with origin={skill,args}; resumable_action rebuilds Route(origin.skill, {**origin.args, entity_id, name}),
falling back to entity_card when no origin. Spec: 2026-07-04-a9-disambig-origin-skill-design.md.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.router import Route, route
from v2.core.retrieval.slot_extractor import resolve_and_validate
from v2.core.retrieval.structured_answer import resumable_action, run, format_answer


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/wang1", name="Guiling Wang", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    project_appointment(c, person_key="d/wang2", name="Jian Wang", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    # two people sharing an IDENTICAL full name → exercises the named-multi disambig branch
    project_appointment(c, person_key="d/kim1", name="Alex Kim", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    project_appointment(c, person_key="d/kim2", name="Alex Kim", org_id=cs, category="staff",
                        titles=["Advisor"], source_section="manual", source="dashboard")
    c.commit()
    yield c
    c.close()


_CANDS = [{"entity_id": 11, "name": "Guiling Wang"}, {"entity_id": 22, "name": "Jian Wang"}]


# ═══════════════ resumable_action — the resume rebuild (pure, no DB) ═══════════════
def test_a9_resumable_rebuilds_origin_skill():
    rt = Route("person_disambig", {"candidates": _CANDS,
                                    "origin": {"skill": "metric_of_person",
                                               "args": {"field_key": "scholar", "metric_key": "h_index"}}})
    opts = resumable_action(rt)
    assert opts is not None
    assert [label for label, _ in opts] == ["Guiling Wang", "Jian Wang"]
    for label, r in opts:
        assert r.skill == "metric_of_person"                      # NOT entity_card
        assert r.args["metric_key"] == "h_index" and r.args["field_key"] == "scholar"
        assert r.args["entity_id"] in (11, 22) and r.args["name"] == label


def test_a9_resumable_no_origin_falls_back_to_entity_card():
    opts = resumable_action(Route("person_disambig", {"candidates": _CANDS}))
    assert opts is not None
    assert all(r.skill == "entity_card" for _, r in opts)


def test_a9_resumable_resolved_id_wins_over_stale_origin_arg():
    """The resolved entity_id/name must override any (defensive) collision in origin.args."""
    rt = Route("person_disambig", {"candidates": _CANDS,
                                    "origin": {"skill": "metric_of_person",
                                               "args": {"entity_id": 999, "name": "STALE"}}})
    for label, r in resumable_action(rt):
        assert r.args["entity_id"] in (11, 22) and r.args["name"] == label   # not 999 / STALE


# ═══════════════ producer: router.route() (DB) ═══════════════
def test_a9_router_metric_disambig_carries_origin(conn):
    r = route(conn, "Wang h-index")
    assert isinstance(r, Route) and r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "metric_of_person"


def test_a9_router_contact_disambig_carries_origin(conn):
    r = route(conn, "Wang email")
    assert isinstance(r, Route) and r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "contact_of_person"       # _person_skill → contact cue


def test_a9_router_research_disambig_carries_origin(conn):
    r = route(conn, "Wang research")
    assert isinstance(r, Route) and r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "research_of_person"


def test_a9_router_named_multi_disambig_carries_person_skill_origin(conn):
    """Two people with the SAME full name in the query → named-multi branch; origin pins to
    _person_skill(q) (contact cue here), NOT blanket entity_card."""
    r = route(conn, "Alex Kim email")
    assert isinstance(r, Route) and r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "contact_of_person"


# ═══════════════ producer: slot_extractor.resolve_and_validate (DB) ═══════════════
def test_a9_slot_metric_disambig_carries_origin(conn):
    r = resolve_and_validate(conn, "metric_of_person",
                             {"person": "Wang", "metric": "h_index"}, "wang h-index")
    assert r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "metric_of_person"


def test_a9_slot_contact_disambig_carries_origin(conn):
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Wang"}, "wang email")
    assert r.skill == "person_disambig"
    assert r.args["origin"]["skill"] == "contact_of_person"


# ═══════════════ display invariant ═══════════════
def test_a9_display_unchanged_by_origin(conn):
    a = format_answer(run(conn, Route("person_disambig", {"candidates": _CANDS})))
    b = format_answer(run(conn, Route("person_disambig",
                                      {"candidates": _CANDS, "origin": {"skill": "metric_of_person", "args": {}}})))
    assert a == b and a  # byte-identical + non-empty

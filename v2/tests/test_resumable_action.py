"""Tests for resumable_action: converting offer/clarify skills to resume options."""

from v2.core.retrieval.router import Route
from v2.core.retrieval import structured_answer as sa


def test_metric_decline_produces_top_metric_option():
    rt = Route("metric_descending_unsupported",
               {"field_key": "scholar.citations", "metric_key": "citations",
                "org_id": 5, "n": 1, "org_defaulted": False})
    opts = sa.resumable_action(rt)
    assert opts is not None and len(opts) == 1
    label, route = opts[0]
    assert "citation" in label.lower()
    assert route.skill == "top_people_by_metric"
    assert route.args["org_id"] == 5 and route.args["metric_key"] == "citations"


def test_person_disambig_produces_one_option_per_candidate():
    cands = [{"entity_id": 11, "name": "Ada Lovelace"}, {"entity_id": 22, "name": "Alan Turing"}]
    rt = Route("person_disambig", {"candidates": cands})
    opts = sa.resumable_action(rt)
    assert [l for l, _ in opts] == ["Ada Lovelace", "Alan Turing"]
    assert opts[1][1].skill == "entity_card" and opts[1][1].args["entity_id"] == 22


def test_other_skill_is_not_resumable():
    rt = Route("faculty_in_department", {"org_id": 5})
    assert sa.resumable_action(rt) is None


def test_metric_decline_with_no_org_is_not_resumable():
    rt = Route("metric_descending_unsupported",
               {"field_key": "scholar.citations", "metric_key": "citations",
                "org_id": None, "n": 1, "org_defaulted": False})
    assert sa.resumable_action(rt) is None

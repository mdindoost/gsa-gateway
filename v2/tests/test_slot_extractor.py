"""Tests for the constrained-JSON slot-extraction fallback (Workstream 1).

Split of concerns (design §7 Option A):
- extract_slots is tested with a STUB generator (the LLM's real extraction quality is measured by the
  bakeoff, not unit tests).
- resolve_and_validate is tested against a real in-memory KG — it is the safety boundary: it must
  reject hallucinated/unresolved slots and never emit a Route with an unvalidated slot.
"""
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
from v2.core.retrieval.router import Route
from v2.core.retrieval.slot_extractor import (
    KG_SKILL_NAMES, build_schema, extract_slots, resolve_and_validate,
)


# ── schema / shared registry ─────────────────────────────────────────────────────────────────────
def test_schema_enum_is_registry_plus_none():
    enum = build_schema()["properties"]["skill"]["enum"]
    assert enum == list(KG_SKILL_NAMES) + ["none"]


def test_deferred_skills_not_in_enum():
    assert "papers_of_person" not in KG_SKILL_NAMES
    assert "citation_trend_of_person" not in KG_SKILL_NAMES


def test_valid_skills_shares_registry():
    from v2.eval.router.dataset import VALID_SKILLS
    assert set(KG_SKILL_NAMES) <= VALID_SKILLS
    assert VALID_SKILLS == set(KG_SKILL_NAMES) | {"person_disambig"}


# ── extract_slots fail-safe (stub generator) ─────────────────────────────────────────────────────
def test_extract_none_on_generator_none():
    assert extract_slots("x", lambda s, p, sc: None).skill == "none"


def test_extract_none_on_generator_raise():
    def boom(s, p, sc):
        raise RuntimeError("ollama down")
    assert extract_slots("x", boom).skill == "none"


def test_extract_none_on_unknown_skill():
    r = extract_slots("x", lambda s, p, sc: {"skill": "make_coffee", "slots": {}, "confidence": 1})
    assert r.skill == "none"


@pytest.mark.parametrize("msg", [
    "What about for BME?",
    "What about michael giorgio",
    "Who else in mechanical engineering citation you have",
    "how about the math department",
    "and for CS?",
])
def test_extract_abstains_on_followups(msg):
    # anaphoric follow-ups must abstain WITHOUT calling the generator (hardneg regression from the gate)
    def boom(s, p, sc):
        raise AssertionError("generator must not be called on a follow-up")
    assert extract_slots(msg, boom).skill == "none"


def test_extract_parses_and_cleans():
    payload = {"skill": "entity_card",
               "slots": {"person": " Koutis ", "bogus": "drop", "n": 3},
               "confidence": "0.9"}
    r = extract_slots("who is koutis", lambda s, p, sc: payload)
    assert r.skill == "entity_card"
    assert r.slots == {"person": "Koutis", "n": 3}      # trimmed; unknown key dropped
    assert r.confidence == 0.9                           # coerced from str


# ── resolve_and_validate against a real KG ───────────────────────────────────────────────────────
@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    # alias 'computing' on YWCC so natural-text _find_org resolves "in computing"
    c.execute("UPDATE organizations SET metadata=? WHERE slug='ywcc'", ('{"aliases": ["computing"]}',))
    sync_org_nodes(c)

    def appt(key, name, org, title, cat="faculty"):
        project_appointment(c, person_key=key, name=name, org_id=org, category=cat,
                            titles=[title], source_section="manual", source="dashboard")
    appt("d/koutis", "Ioannis Koutis", cs, "Professor")
    appt("d/kdean", "Craig Gotsman", ywcc, "Dean", cat="admin")
    appt("d/wang1", "Guiling Wang", cs, "Professor")
    appt("d/wang2", "Jian Wang", cs, "Professor")
    c.commit()
    yield c
    c.close()


def _oid(conn, slug):
    return conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()[0]


def test_entity_card_resolves_person(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Koutis"}, "who is koutis")
    assert isinstance(r, Route) and r.skill == "entity_card"
    assert r.args["entity_id"] == "d/koutis"


def test_entity_card_fires_on_bare_name(conn):
    assert resolve_and_validate(conn, "entity_card", {"person": "Koutis"}, "koutis").skill == "entity_card"


def test_entity_card_abstains_on_fragment_with_stray_token(conn):
    # "mmi ioannis koutis" only INCIDENTALLY contains the name; no identity cue → must abstain
    assert resolve_and_validate(conn, "entity_card", {"person": "Koutis"}, "mmi ioannis koutis") is None


def test_ambiguous_person_becomes_disambig(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Wang"}, "who is wang")
    assert r.skill == "person_disambig"
    assert len(r.args["candidates"]) == 2


def test_unknown_person_abstains(conn):
    assert resolve_and_validate(conn, "entity_card", {"person": "Nobody McGhost"}, "x") is None


def test_missing_required_slot_abstains(conn):
    assert resolve_and_validate(conn, "metric_of_person", {"person": "Koutis"}, "x") is None  # no metric


def test_metric_of_person_maps_keys(conn):
    r = resolve_and_validate(conn, "metric_of_person",
                             {"person": "Koutis", "metric": "citations"}, "koutis citations")
    assert r.skill == "metric_of_person"
    assert r.args["field_key"] == "scholar" and r.args["metric_key"] == "citations"


def test_least_cited_declines(conn):
    # "least/fewest" = ascending (lowest-first) = order:asc → the unsupported direction.
    r = resolve_and_validate(conn, "top_people_by_metric",
                             {"metric": "citations", "order": "asc"}, "least cited in cs")
    assert r.skill == "metric_descending_unsupported"


def test_most_cited_desc_executes(conn):
    # "most cited / top N" = descending (highest-first) = order:desc → SUPPORTED, must execute.
    r = resolve_and_validate(conn, "top_people_by_metric",
                             {"metric": "citations", "order": "desc"}, "most cited in cs")
    assert r.skill == "top_people_by_metric"
    assert r.args["metric_key"] == "citations"


def test_role_climbs_dept_to_college(conn):
    r = resolve_and_validate(conn, "people_by_role", {"role": "dean", "org": "cs"},
                             "who is the dean of cs")
    assert r.skill == "people_by_role" and r.args["role_head"] == "dean"
    assert r.args["org_id"] == _oid(conn, "ywcc")        # climbed dept→college


def test_org_skill_unknown_org_abstains(conn):
    assert resolve_and_validate(conn, "faculty_in_department", {"org": "atlantis"}, "x") is None


def test_org_departments_leaf_abstains(conn):
    # cs is a leaf department (no child departments) → route()'s guard must apply
    assert resolve_and_validate(conn, "org_departments", {"org": "cs"}, "departments in cs") is None


def test_people_in_org_university_root_abstains(conn):
    assert resolve_and_validate(conn, "people_in_org", {"org": "njit"}, "people at njit") is None


def test_bare_area_no_support_abstains(conn):
    # nonsense area with 0 KG faculty support → must abstain (area false-positive guard)
    assert resolve_and_validate(conn, "people_by_research_area",
                                {"area": "quantum blockchain yoga"}, "x") is None


def test_area_with_org_bypasses_support_guard(conn):
    # org present → executes without the bare-area support check
    r = resolve_and_validate(conn, "people_by_research_area",
                             {"area": "machine learning", "org": "computing"},
                             "which prof does ml in computing")
    assert r.skill == "people_by_research_area"
    assert r.args["org_id"] == _oid(conn, "ywcc") and r.args["area"] == "machine learning"


def test_top_metric_defaults_to_root_when_no_org(conn):
    r = resolve_and_validate(conn, "top_people_by_metric", {"metric": "h_index"},
                             "most cited professor")
    assert r.skill == "top_people_by_metric"
    assert r.args["org_id"] == _oid(conn, "njit") and r.args.get("org_defaulted") is True


# ── the 3 motivating regression paraphrases (resolve layer, extraction stubbed to correct) ────────
def test_regression_ml_in_computing(conn):
    r = resolve_and_validate(conn, "people_by_research_area",
                             {"area": "machine learning", "org": "computing"},
                             "which prof does ML in computing")
    assert r.skill == "people_by_research_area"


def test_regression_tell_me_about_koutis(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Koutis"},
                             "can you tell me a bit about professor Koutis?")
    assert r.skill == "entity_card" and r.args["entity_id"] == "d/koutis"


def test_regression_reach_someone_named_koutis(conn):
    r = resolve_and_validate(conn, "entity_card", {"person": "Koutis"},
                             "I'm trying to reach someone named Koutis")
    assert r.skill == "entity_card" and r.args["entity_id"] == "d/koutis"

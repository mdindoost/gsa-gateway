"""A3 — person_names_of(result): tag-at-source name extraction across the heterogeneous skill
result shapes. Person-bearing skills yield the roster; area/org/count/unknown skills yield [].
Spec: docs/superpowers/specs/2026-07-04-a3-antecedent-ambiguity-design.md"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.retrieval.structured_answer import person_names_of


# ── roster skills — string-name rows ──────────────────────────────────────────
def test_faculty_in_department_string_rows():
    r = {"skill": "faculty_in_department", "rows": ["Ana Rolim", "Bryan Pfister", "Xin Di"]}
    assert person_names_of(r) == ["Ana Rolim", "Bryan Pfister", "Xin Di"]


def test_people_by_research_area_string_rows():
    r = {"skill": "people_by_research_area", "rows": ["Ana Rolim", "Bharat Biswal"]}
    assert person_names_of(r) == ["Ana Rolim", "Bharat Biswal"]


# ── roster skills — name-first tuple rows ─────────────────────────────────────
def test_officers_in_org_tuple_rows():
    r = {"skill": "officers_in_org",
         "rows": [("Jane Doe", "President", "jd@njit.edu"), ("Sam Roe", "VP", None)]}
    assert person_names_of(r) == ["Jane Doe", "Sam Roe"]


def test_people_by_role_quad_tuple_rows():
    r = {"skill": "people_by_role",
         "rows": [("Ada Lovelace", "Dean", "YWCC", "a@njit.edu"),
                  ("Alan Turing", "Dean", "NCE", None)]}
    assert person_names_of(r) == ["Ada Lovelace", "Alan Turing"]


# ── dict rows ─────────────────────────────────────────────────────────────────
def test_people_by_name_dict_rows():
    r = {"skill": "people_by_name",
         "rows": [{"name": "Bryan Pfister", "title": "Professor"}, {"name": "Bryan Ng"}]}
    assert person_names_of(r) == ["Bryan Pfister", "Bryan Ng"]


# ── candidate lists ───────────────────────────────────────────────────────────
def test_person_disambig_candidates_are_people():
    r = {"skill": "person_disambig",
         "candidates": [{"name": "Mark Cartwright", "entity_id": "1"},
                        {"name": "Mark Smith", "entity_id": "2"}]}
    assert person_names_of(r) == ["Mark Cartwright", "Mark Smith"]


def test_org_disambig_candidates_are_not_people():
    r = {"skill": "org_disambig",
         "candidates": [{"name": "Computer Science", "org_id": 1},
                        {"name": "Chemistry", "org_id": 2}]}
    assert person_names_of(r) == []


# ── single-person skills ──────────────────────────────────────────────────────
def test_entity_card_single_name():
    assert person_names_of({"skill": "entity_card", "name": "Guiling Wang"}) == ["Guiling Wang"]


def test_entity_card_missing_name_is_empty():
    assert person_names_of({"skill": "entity_card", "name": None}) == []


def test_metric_of_person_single_name():
    assert person_names_of({"skill": "metric_of_person", "name": "Bryan Pfister"}) == ["Bryan Pfister"]


def test_research_of_person_nested_name():
    r = {"skill": "research_of_person", "research": {"name": "Guiling Wang", "areas": ["AI"]}}
    assert person_names_of(r) == ["Guiling Wang"]


def test_top_people_by_metric_ranked_rows():
    r = {"skill": "top_people_by_metric", "ranked": [("Guiling Wang", 5000), ("Xin Di", 3000)]}
    assert person_names_of(r) == ["Guiling Wang", "Xin Di"]


def test_faculty_areas_in_department_roster_fallback():
    # nobody lists areas → rows empty, roster carries names
    r = {"skill": "faculty_areas_in_department", "rows": [], "roster": ["Ana Rolim", "Xin Di"]}
    assert person_names_of(r) == ["Ana Rolim", "Xin Di"]
    # with areas present → names come from the (name, areas) rows
    r2 = {"skill": "faculty_areas_in_department",
          "rows": [("Ana Rolim", ["AI"]), ("Xin Di", ["fMRI"])], "roster": []}
    assert person_names_of(r2) == ["Ana Rolim", "Xin Di"]


# ── NON-person skills → [] (Fable traps) ──────────────────────────────────────
def test_count_int_rows_no_crash():
    assert person_names_of({"skill": "count_people_by_research_area", "rows": 11, "area": "brain"}) == []


def test_areas_in_org_strings_are_not_people():
    assert person_names_of({"skill": "areas_in_org", "rows": ["Machine Learning", "Robotics"]}) == []


def test_orgs_by_type_strings_are_not_people():
    assert person_names_of({"skill": "orgs_by_type", "rows": ["GSA", "WiCS"], "org_type": "club"}) == []


def test_unknown_skill_defaults_empty():
    assert person_names_of({"skill": "some_future_skill", "rows": ["A, B, C"]}) == []


def test_non_dict_is_empty():
    assert person_names_of(None) == []
    assert person_names_of("nope") == []

# v2/tests/test_ws3_render.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.retrieval import structured_answer as sa


def test_contact_full_render():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Ioannis Koutis",
                            "email": "ik@njit.edu", "phone": "973-555-0101", "office": "GITC 4400",
                            "present": ["email", "phone", "office"]})
    assert "ik@njit.edu" in out and "973-555-0101" in out and "GITC 4400" in out


def test_contact_partial_states_missing():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Ola Office",
                            "email": None, "phone": None, "office": "GITC 1000", "present": ["office"]})
    assert "GITC 1000" in out
    assert "email" in out.lower() and "phone" in out.lower()  # explicitly names what's missing


def test_contact_none_on_file():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Nadia Noattr",
                            "email": None, "phone": None, "office": None, "present": []})
    assert "don't have" in out.lower() or "not on file" in out.lower()


def test_title_render():
    out = sa.format_answer({"skill": "title_of_person", "name": "Ioannis Koutis",
                            "titles": [("Professor", "Computer Science"),
                                       ("Department Chair", "Computer Science")]})
    assert "Professor" in out and "Department Chair" in out and "Computer Science" in out


def test_title_category_fallback_reads_ok():
    # a category-only title ("faculty") renders as a title-listing, not "is faculty at" (review MINOR)
    out = sa.format_answer({"skill": "title_of_person", "name": "Nadia Noattr",
                            "titles": [("faculty", "Computer Science")]})
    assert "faculty" in out and "Computer Science" in out


def test_title_empty_render():
    out = sa.format_answer({"skill": "title_of_person", "name": "No Role", "titles": []})
    assert "don't have" in out.lower()


def test_orgs_by_type_count_and_list():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "club", "parent_name": None,
                            "rows": ["ACM Student Chapter", "Women in Computing Society"]})
    assert "2" in out and "ACM Student Chapter" in out and "Women in Computing Society" in out


def test_orgs_by_type_singular_grammar():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "college", "parent_name": None,
                            "rows": ["Ying Wu College of Computing"]})
    assert "1 college" in out and "colleges" not in out  # explicit singular, not a plural hack


def test_orgs_by_type_empty():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "club", "parent_name": None,
                            "rows": []})
    assert "don't have" in out.lower()


def test_contact_orgs_compose_not_deterministic():
    # owner decision: contact/orgs COMPOSE (keep the greeting) — must NOT be verbatim-only.
    # (title_of_person moved to deterministic 2026-07-05: it now carries the load-bearing
    # affiliated/joint marker, which the LLM must not reword — see affiliated-faculty design.)
    for skill in ("contact_of_person", "orgs_by_type"):
        assert not sa.is_deterministic({"skill": skill})


def test_title_of_person_now_deterministic():
    assert sa.is_deterministic({"skill": "title_of_person"}) is True

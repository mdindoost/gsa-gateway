"""TDD — Gap #1: person-scoped yes/no `does_person_research_area`.

"is he working on machine learning?" (he=Koutis, resolved upstream) must yield a per-person yes/no
that is PROVABLY consistent with the population skill (people_by_research_area), renders honest-partial
when the person lists no areas, and NEVER over-claims a 'listed' area when the match came from prose.
Spec: docs/superpowers/specs/2026-07-04-does-person-research-area-design.md
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval import skills


def _person(conn, entity_id, name):
    conn.execute("INSERT INTO nodes(type,key,name,source,is_active) VALUES('Person',?,?,'crawler',1)", (entity_id, name))
    conn.commit()


def _person_area(conn, org_id, entity_id, name, areas):
    _person(conn, entity_id, name)
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_areas", name, f"Research areas of {name}: {'; '.join(areas)}",
         json.dumps({"entity_id": entity_id, "areas": areas}), "crawler"))
    conn.commit()


def _person_statement(conn, org_id, entity_id, name, text):
    """A person with a research_statement (prose) but NO discrete area tags."""
    _person(conn, entity_id, name)
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_statement", name, f"Research statement of {name}: {text}",
         json.dumps({"entity_id": entity_id}), "crawler"))
    conn.commit()


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(3,1,'Computer Science','cs','department')")
    # Koutis LISTS machine learning as a tag → tag-confirmed yes.
    _person_area(c, 3, "p/koutis", "Ioannis Koutis", ["machine learning", "graph theory"])
    # Quinn lists only quantum → 'no' for machine learning, but HAS areas to show.
    _person_area(c, 3, "p/quinn", "Quinn Quantum", ["quantum computing"])
    # Val has a research_statement mentioning ML but NO discrete area tags → prose-yes / honest.
    _person_statement(c, 3, "p/val", "Val Vision",
                      "My work touches computer vision with applications in machine learning.")
    # Nora has NO research data at all → honest-partial 'unknown'.
    _person(c, "p/nora", "Nora None")
    yield c
    c.close()


# ═══════════════ answer classification ═══════════════
def test_tag_confirmed_yes(db):
    r = skills.does_person_research_area(db, "p/koutis", "machine learning")
    assert r["answer"] == "yes"
    assert r["basis"] == "tag"
    assert r["name"] == "Ioannis Koutis"
    assert "machine learning" in [a.lower() for a in r["person_areas"]]
    # the matched canonical TAG is echoed back so the renderer shows the real tag, not the raw query.
    assert r["matched_area"] is not None and "machine learning" in r["matched_area"].lower()


def test_matched_area_none_off_the_tag_path(db):
    assert skills.does_person_research_area(db, "p/val", "machine learning")["matched_area"] is None   # prose
    assert skills.does_person_research_area(db, "p/quinn", "machine learning")["matched_area"] is None  # no
    assert skills.does_person_research_area(db, "p/nora", "machine learning")["matched_area"] is None   # unknown


def test_prose_only_yes_is_not_tag(db):
    # Val is in the ML set via STATEMENT prose but lists no ML tag → yes, but basis='prose' so the
    # renderer must NOT claim he 'lists' ML (the anti-fabrication / self-contradiction guard).
    r = skills.does_person_research_area(db, "p/val", "machine learning")
    assert r["answer"] == "yes"
    assert r["basis"] == "prose"
    assert "machine learning" not in [a.lower() for a in r["person_areas"]]


def test_no_when_person_has_other_areas(db):
    r = skills.does_person_research_area(db, "p/quinn", "machine learning")
    assert r["answer"] == "no"
    assert r["basis"] is None
    assert "quantum computing" in [a.lower() for a in r["person_areas"]]


def test_unknown_when_no_areas_listed(db):
    # honest-partial: no tags AND no prose hit → can't confirm, must NOT say 'no'.
    r = skills.does_person_research_area(db, "p/nora", "machine learning")
    assert r["answer"] == "unknown"
    assert r["person_areas"] == []


def test_name_override_arg_wins(db):
    r = skills.does_person_research_area(db, "p/koutis", "machine learning", name="Prof. Koutis")
    assert r["name"] == "Prof. Koutis"


def test_synonym_abbreviation_matches_tag(db):
    # R3: "ml" must expand (AREA_SYNONYMS) to match the "machine learning" TAG — a yes, tag-confirmed,
    # and matched_area echoes the REAL canonical tag, not the raw "ml".
    r = skills.does_person_research_area(db, "p/koutis", "ml")
    assert r["answer"] == "yes"
    assert r["basis"] == "tag"
    assert r["matched_area"] is not None and "machine learning" in r["matched_area"].lower()


# ═══════════════ the consistency invariant (the core thesis) ═══════════════
def test_membership_matches_population_skill_exactly(db):
    """For every person, does_person_research_area == 'yes' IFF they appear in
    people_by_research_area(area). The two can never disagree."""
    area = "machine learning"
    roster_ids = {eid for _n, eid in skills.people_by_research_area(db, area)}
    for eid in ("p/koutis", "p/quinn", "p/val", "p/nora"):
        yes = skills.does_person_research_area(db, eid, area)["answer"] == "yes"
        assert yes == (eid in roster_ids), f"{eid}: skill={yes} roster={eid in roster_ids}"


def test_no_answer_never_contradicts_listed_areas(db):
    """Guard (N1): an 'answer==no' must never co-occur with the asked area word-matching the
    person's own listed areas — else we'd deny something they list."""
    for eid in ("p/koutis", "p/quinn", "p/val", "p/nora"):
        for area in ("machine learning", "quantum computing", "graph theory"):
            r = skills.does_person_research_area(db, eid, area)
            if r["answer"] == "no":
                assert not any(area.lower() in a.lower() for a in r["person_areas"])

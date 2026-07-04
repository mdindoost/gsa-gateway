"""TDD — A15: topic→people routing (validated loose-area).

Natural phrasings ("neuroscience faculty", "faculty in neuroscience", "professors doing neuroscience")
must reach the deterministic people_by_research_area skill, not RAG — BUT only when the topic is a real
research area (someone LISTS it as a tag), never hijacking an org or dumping on a facet word.
Spec: docs/superpowers/specs/2026-07-04-a15-topic-people-routing-design.md
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
from v2.core.retrieval.router import route, Route


def _person_area(conn, org_id, entity_id, name, areas):
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_areas", name, f"Research areas of {name}: {'; '.join(areas)}",
         json.dumps({"entity_id": entity_id, "areas": areas}), "crawler"))
    conn.commit()


def _person_statement(conn, org_id, entity_id, name, text):
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
              "VALUES(2,1,'Ying Wu College of Computing','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(3,2,'Computer Science','cs','department')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(4,1,'Martin Tuchman School of Management','mtsm','college')")
    _person_area(c, 3, "p/neuro", "Nadia Neuro", ["neuroscience"])
    _person_area(c, 3, "p/ml", "Mike Learn", ["machine learning"])
    _person_area(c, 3, "p/graph", "Gina Graph", ["graph theory"])
    _person_area(c, 3, "p/mgmt", "Manny Mgmt", ["management"])          # a real 'management' AREA tag …
    # … but 'management' also fuzzy-matches the MTSM org → the guard must block "management faculty".
    _person_statement(c, 3, "p/vis", "Val Visit", "a visiting scholar working on assorted topics")
    # a person named to collide with a topic-first candidate ("koutis") — R1 must NOT validate on the name
    _person_area(c, 3, "p/koutis", "Ivan Koutis", ["algorithms"])
    # tags whose VALUES contain the people-qualifier words new/international (R2 must stop the bare word)
    _person_area(c, 3, "p/np", "Ned Product", ["new product development"])
    _person_area(c, 3, "p/intl", "Ida Fin", ["International Finance"])
    # topic appears ONLY INSIDE a multi-word tag → R1 word-in-tag (not whole-tag) must still validate it
    _person_area(c, 3, "p/cneuro", "Cara Comp", ["computational neuroscience"])
    yield c
    c.close()


# ═══════════════ validator (skills.is_listed_research_area) ═══════════════
def test_validator_true_for_listed_area(db):
    assert skills.is_listed_research_area(db, "neuroscience") is True
    assert skills.is_listed_research_area(db, "machine learning") is True


def test_validator_tags_only_not_prose(db):
    # "visiting" appears in a research_statement but is nobody's TAG → must be False (tags-only, Fable #1),
    # while the 3-type skill matcher DOES see it (proving the distinction matters).
    assert skills._research_entities(db, "visiting", None)          # 3-type sees the statement
    assert skills.is_listed_research_area(db, "visiting") is False   # tags-only does not


def test_validator_false_for_unknown(db):
    assert skills.is_listed_research_area(db, "the news") is False


# ═══════════════ the 6 previously-failing phrasings → people_by_research_area ═══════════════
@pytest.mark.parametrize("q,area", [
    ("faculty working neuroscience", "neuroscience"),
    ("faculty in neuroscience", "neuroscience"),
    ("neuroscience faculty", "neuroscience"),
    ("professors who study neuroscience", "neuroscience"),
    ("professors doing neuroscience", "neuroscience"),
    ("list faculty in machine learning", "machine learning"),
])
def test_loose_topic_people_routes_to_area(db, q, area):
    r = route(db, q)
    assert isinstance(r, Route) and r.skill == "people_by_research_area", f"{q!r} -> {r}"
    assert area in r.args["area"].lower()


def test_the_determiner_stripped_topic_first(db):
    r = route(db, "the neuroscience faculty")
    assert isinstance(r, Route) and r.skill == "people_by_research_area"
    assert "neuroscience" in r.args["area"].lower()


# ═══════════════ precision negatives (must NOT become people_by_research_area) ═══════════════
def _not_area(r):
    return not (isinstance(r, Route) and r.skill == "people_by_research_area")


@pytest.mark.parametrize("q", [
    "faculty in the news",        # bare-in keeps "the news" → no tag → RAG
    "senior faculty",             # not an area
    "visiting faculty",           # prose-only word, tags-only rejects
    "professors doing research",  # facet stoplist (#3)
    "most cited faculty",         # rank cue excluded (#6) → metric/other, not area
])
def test_precision_negatives(db, q):
    assert _not_area(route(db, q)), q


def test_management_faculty_blocked_by_fuzzy_org(db):
    # 'management' IS a real area tag here, but it fuzzy-matches the MTSM org → guard #2 blocks it.
    assert _not_area(route(db, "management faculty"))


def test_coordinator_role_not_area(db):
    # people-noun 'faculty' is NOT the final token → topic-first can't fire; it's a role ask.
    assert _not_area(route(db, "who is the neuroscience faculty coordinator"))


def test_name_topic_not_area_R1(db):
    # "koutis faculty" must NOT validate — 'koutis' is a NAME, not a tag value (R1 reads tags, not names)
    assert skills.is_listed_research_area(db, "koutis") is False
    assert _not_area(route(db, "koutis faculty"))


@pytest.mark.parametrize("q,word", [("new faculty", "new"), ("faculty in new", "new"),
                                    ("international faculty", "international")])
def test_people_qualifier_word_not_area_R2(db, q, word):
    # 'new'/'international' match word-boundary INSIDE real tags ("new product development",
    # "International Finance") — the validator WOULD say yes, so R2 facet-stop must block the bare word.
    assert skills.is_listed_research_area(db, word) is True      # the word does match a real tag …
    assert _not_area(route(db, q)), q                            # … yet the bare-qualifier query → RAG


def test_topic_inside_multiword_tag_validates_R1(db):
    # 'neuroscience' appears only INSIDE "computational neuroscience" for p/cneuro → word-in-tag must hit
    assert skills.is_listed_research_area(db, "neuroscience") is True
    r = route(db, "neuroscience faculty")
    assert isinstance(r, Route) and r.skill == "people_by_research_area"


def test_multiword_qualifier_topic_still_works(db):
    # only the BARE qualifier is stopped — a real multi-word area starting with it still routes
    assert skills.is_listed_research_area(db, "new product development") is True
    r = route(db, "new product development faculty")
    assert isinstance(r, Route) and r.skill == "people_by_research_area"


def test_cs_faculty_is_department_not_area(db):
    r = route(db, "CS faculty")
    assert isinstance(r, Route) and r.skill == "faculty_in_department" and r.args.get("org_id") == 3


# ═══════════════ count intent + org scope ═══════════════
def test_how_many_loose_routes_to_count(db):
    r = route(db, "how many faculty work in neuroscience")
    assert isinstance(r, Route) and r.skill == "count_people_by_research_area"


def test_org_scoped_loose_area(db):
    r = route(db, "machine learning faculty in ywcc")
    assert isinstance(r, Route) and r.skill == "people_by_research_area"
    assert "machine learning" in r.args["area"].lower() and r.args.get("org_id") == 2


# ═══════════════ no-regression: strict path still routes ═══════════════
@pytest.mark.parametrize("q", ["who works on graph theory", "researchers in machine learning",
                               "who studies neuroscience"])
def test_strict_path_unchanged(db, q):
    r = route(db, q)
    assert isinstance(r, Route) and r.skill == "people_by_research_area"

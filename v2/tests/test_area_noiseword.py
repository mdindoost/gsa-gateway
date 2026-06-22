"""Area-enumeration noise-word fix (accuracy backlog #6A).

"who works on graph research" extracts area="graph research" → FTS phrase matches nobody (no faculty text
contains that literal phrase) → empty → structured deflection. Fix (D2): _research_entities retries once on
the stripped term ("graph") ONLY when the full term is empty AND ends in a redundant facet word.

Spec: docs/superpowers/specs/2026-06-22-area-enumeration-noiseword-fix-design.md
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


def _person_area(conn, org_id, entity_id, name, areas):
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_areas", name, f"Research areas of {name}: {'; '.join(areas)}",
         json.dumps({"entity_id": entity_id, "areas": areas}), "crawler"))
    conn.commit()


@pytest.fixture()
def db():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(2,1,'CS','cs','department')")
    # NOTE: names deliberately do NOT end in "graph"/"research" — search_text = title||content, so a
    # title ending in "Graph" + content starting "Research areas" would fake a "graph research" adjacency.
    _person_area(c, 2, "p/graph", "Pat Example", ["Graph Theory"])
    _person_area(c, 2, "p/ops", "Olive Vance", ["operations research"])
    _person_area(c, 2, "p/quant", "Quinn West", ["Quantum Computing"])
    yield c
    c.close()


def test_graph_research_noiseword_resolves_to_roster(db):
    # the bug: "graph research" as a phrase matches nobody; the fix retries on "graph"
    assert skills._research_entities(db, "graph research", None) == {"p/graph"}
    assert skills._research_entities(db, "graph", None) == {"p/graph"}


def test_list_equals_count(db):
    assert skills.count_people_by_research_area(db, "graph research", None) == 1
    assert (skills.count_people_by_research_area(db, "graph research", None)
            == skills.count_people_by_research_area(db, "graph", None))


def test_operations_research_not_broadened(db):
    # "operations research" resolves on the full phrase → retry does NOT fire → only the ops person,
    # never a broadened "operations" set.
    assert skills._research_entities(db, "operations research", None) == {"p/ops"}


def test_unknown_area_stays_empty(db):
    # no facet suffix + no match → regex doesn't fire → honest empty (honest-partial)
    assert skills._research_entities(db, "quantum teleportation", None) == set()


def test_degenerate_research_terms_do_not_crash(db):
    # bare "research" / "research areas": regex needs a leading token → no retry, no empty-FTS-term, no crash
    assert isinstance(skills._research_entities(db, "research", None), set)
    assert isinstance(skills._research_entities(db, "research areas", None), set)


def test_org_scoped_retry_preserves_org(db):
    # the retry must keep org_id: graph person is in cs(2); scoping to a sibling dept finds nobody
    assert skills._research_entities(db, "graph research", 2) == {"p/graph"}
    db.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(3,1,'Math','math','department')")
    db.commit()
    assert skills._research_entities(db, "graph research", 3) == set()

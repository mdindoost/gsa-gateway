"""TDD — Gap #1 render + wiring: does_person_research_area through structured_answer.

Deterministic (no LLM reword), basis-aware wording (never claims 'lists' on a prose match),
honest-partial 'unknown', and A3 single-name tagging so the NEXT pronoun follow-up keeps its antecedent.
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
from v2.core.retrieval import structured_answer as SA
from v2.core.retrieval.router import Route


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
    _person(conn, entity_id, name)
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_statement", name, f"Research statement of {name}: {text}",
         json.dumps({"entity_id": entity_id}), "crawler"))
    conn.commit()


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(5,1,'Computer Science','cs','department')")
    _person_area(c, 5, "p/koutis", "Ioannis Koutis", ["machine learning", "graph theory"])
    _person_area(c, 5, "p/quinn", "Quinn Quantum", ["quantum computing"])
    _person_statement(c, 5, "p/val", "Val Vision",
                      "My work touches computer vision with applications in machine learning.")
    _person(c, "p/nora", "Nora None")
    yield c
    c.close()


def _answer(conn, entity_id, name, area):
    res = SA.run(conn, Route("does_person_research_area",
                             {"entity_id": entity_id, "name": name, "area": area}))
    return res, SA.format_answer(res)


# ═══════════════ deterministic + wired ═══════════════
def test_is_deterministic(conn):
    res, _ = _answer(conn, "p/koutis", "Ioannis Koutis", "machine learning")
    assert SA.is_deterministic(res) is True


def test_format_answer_never_empty(conn):
    # empty "" would fall through to RAG — every branch must render.
    for eid, nm in [("p/koutis", "Ioannis Koutis"), ("p/quinn", "Quinn Quantum"),
                    ("p/val", "Val Vision"), ("p/nora", "Nora None")]:
        _res, text = _answer(conn, eid, nm, "machine learning")
        assert text.strip() != ""


def test_person_names_of_tags_single_name(conn):
    # A3: the assistant turn must be tagged with exactly this one person for the next follow-up.
    res, _ = _answer(conn, "p/koutis", "Ioannis Koutis", "machine learning")
    assert SA.person_names_of(res) == ["Ioannis Koutis"]


# ═══════════════ basis-aware wording ═══════════════
def test_tag_yes_says_listed(conn):
    _res, text = _answer(conn, "p/koutis", "Ioannis Koutis", "machine learning")
    low = text.lower()
    assert low.startswith("yes")
    assert "machine learning" in low and "listed research areas" in low
    assert "graph theory" in low          # other areas surfaced


def test_prose_yes_does_not_claim_listed(conn):
    # Val matches via statement prose only — must NOT say he 'lists' ML (anti-fabrication).
    _res, text = _answer(conn, "p/val", "Val Vision", "machine learning")
    low = text.lower()
    assert low.startswith("yes")
    assert "profile" in low                        # "appears in ... research profile"
    assert "lists machine learning" not in low
    assert "among their listed research areas" not in low
    assert "among val vision's listed research areas" not in low


def test_no_lists_the_real_areas(conn):
    _res, text = _answer(conn, "p/quinn", "Quinn Quantum", "machine learning")
    low = text.lower()
    assert "don't see machine learning" in low or "not among" in low
    assert "quantum computing" in low              # mandatory areas list (the hedge)


def test_unknown_is_honest_partial(conn):
    _res, text = _answer(conn, "p/nora", "Nora None", "machine learning")
    low = text.lower()
    assert "nora none" in low
    assert "don't have research areas" in low or "can't confirm" in low
    # must NOT assert a false negative
    assert "no —" not in low and not low.startswith("no")

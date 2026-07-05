"""TDD — Gap #1 router branch: person + area → does_person_research_area.

The motivating query is a bare-SURNAME rewrite ("is koutis working on machine learning") — 6 content
tokens, no full name — which the >4-token surname guard would drop. The branch MUST still resolve the
person via the isolated subject span, and MUST NOT hijack population / org / faculty queries.
Spec: docs/superpowers/specs/2026-07-04-does-person-research-area-design.md
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


def _person_area(conn, org_id, entity_id, name, areas):
    conn.execute("INSERT INTO nodes(type,key,name,source,is_active) VALUES('Person',?,?,'crawler',1)", (entity_id, name))
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,is_active,created_by) "
        "VALUES(?,?,?,?,?,1,1,?)",
        (org_id, "research_areas", name, f"Research areas of {name}: {'; '.join(areas)}",
         json.dumps({"entity_id": entity_id, "areas": areas}), "crawler"))
    conn.commit()


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(5,1,'Computer Science','cs','department')")
    _person_area(c, 5, "p/koutis", "Ioannis Koutis", ["machine learning", "graph theory"])
    _person_area(c, 5, "p/wang1", "Guiling Wang", ["wireless networks"])   # a second Wang for disambig
    _person_area(c, 5, "p/wang2", "Chase Wang", ["databases"])
    # R2: a real person surnamed "He" — so the raw-pronoun guard test is NOT vacuous. Without the
    # _PRONOUN_SUBJ check, "is he working on ML" would surname-mine "he" → Xin He → wrong-person answer.
    _person_area(c, 5, "p/he", "Xin He", ["algorithms"])
    yield c
    c.close()


# ═══════════════ the branch fires on the bare-surname rewrite (B1) ═══════════════
def test_bare_surname_working_on_routes_person_scoped(conn):
    r = route(conn, "is koutis working on machine learning")
    assert r is not None and r.skill == "does_person_research_area"
    assert r.args["entity_id"] == "p/koutis"
    assert r.args["area"] == "machine learning"


def test_bare_surname_with_currently_adverb(conn):
    # the LLM rewrite often inserts 'currently' → subject span "koutis currently" must still resolve.
    r = route(conn, "is koutis currently working on machine learning")
    assert r is not None and r.skill == "does_person_research_area"
    assert r.args["entity_id"] == "p/koutis"


def test_does_verb_person_scoped(conn):
    # R1: use "work on" (matches _AREA_TRIGGER) not bare "research X" (would force widening the trigger).
    r = route(conn, "does koutis work on graph theory")
    assert r is not None and r.skill == "does_person_research_area"
    assert r.args["entity_id"] == "p/koutis"
    assert r.args["area"] == "graph theory"


def test_full_name_also_routes(conn):
    r = route(conn, "is ioannis koutis working on machine learning")
    assert r is not None and r.skill == "does_person_research_area"
    assert r.args["entity_id"] == "p/koutis"


# ═══════════════ neighbors MUST stay on their existing routes ═══════════════
def test_population_who_works_on_unchanged(conn):
    r = route(conn, "who works on machine learning")
    assert r is not None and r.skill == "people_by_research_area"


def test_org_only_does_not_person_route(conn):
    # "does CS work on ML" — predicate fires but subject resolves no person → population, not person-scoped.
    r = route(conn, "does computer science work on machine learning")
    assert r is not None and r.skill == "people_by_research_area"


def test_raw_pronoun_falls_through_no_wrong_person(conn):
    # rewrite-failed raw pronoun: must NEVER resolve to a person (esp. the real faculty surnamed "He").
    r = route(conn, "is he working on machine learning")
    assert r is not None and r.skill == "people_by_research_area"


def test_ambiguous_surname_disambiguates(conn):
    # two Wangs → person_disambig, tagged with the originating skill so the resume re-runs it.
    r = route(conn, "is wang working on databases")
    assert r is not None and r.skill == "person_disambig"
    assert r.args.get("origin", {}).get("skill") == "does_person_research_area"

"""Tests for the entity-centric retrieval layer (v2/core/retrieval/entity.py) and the
new router/structured_answer routes — Phase 1+2 of the retrieval-completeness redesign.

Each behavior is asserted against a small in-memory fixture that mirrors the real DB
shapes (Person nodes with Last,First + First Last names; has_role edges with attrs.titles;
Org nodes bridging attrs.org_id; research_areas docs; a publication to prove exclusion)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval import entity, router, structured_answer as SA


def _org(conn, oid, name, slug, otype, parent=None):
    conn.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
                 (oid, parent, name, slug, otype))
    # bridging Org node
    conn.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Org',?,?,?,'test')",
                 (f"org:{slug}", name, json.dumps({"org_id": oid})))
    return conn.execute("SELECT id FROM nodes WHERE key=?", (f"org:{slug}",)).fetchone()[0]


def _person(conn, key, name, attrs=None):
    conn.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person',?,?,?,'crawler')",
                 (key, name, json.dumps(attrs or {})))
    return conn.execute("SELECT id FROM nodes WHERE key=?", (key,)).fetchone()[0]


def _role(conn, pid, org_node_id, category, titles):
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,attrs,source) "
                 "VALUES(?,'has_role',?,?,?,'crawler')",
                 (pid, org_node_id, category, json.dumps({"titles": titles})))


def _item(conn, org_id, typ, title, content, entity_id, areas=None):
    meta = {"entity_id": entity_id}
    if areas is not None:
        meta["areas"] = areas
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
                 "is_active,created_by) VALUES(?,?,?,?,?,1,1,'crawler')",
                 (org_id, typ, title, content, json.dumps(meta)))


@pytest.fixture
def conn():
    c = create_all(":memory:")
    njit = _org(c, 1, "NJIT", "njit", "university")
    ywcc = _org(c, 2, "YWCC", "ywcc", "college", parent=1)
    inf = _org(c, 3, "Informatics", "informatics", "department", parent=2)
    cs = _org(c, 4, "Computer Science", "computer-science", "department", parent=2)

    payton = _person(c, "p/payton", "Jamie Payton", {"email": "jamie.payton@njit.edu"})
    _role(c, payton, ywcc, "admin", ["Dean, Ying Wu College of Computing"])
    brook = _person(c, "p/wu", "Wu, Brook")
    _role(c, brook, ywcc, "admin", ["Associate Professor", "Associate Dean for Academic Affairs"])

    halper = _person(c, "p/halper", "Halper, Michael")
    _role(c, halper, inf, "faculty", ["Professor"])
    giorgio = _person(c, "p/giorgio", "Michael Giorgio")
    _role(c, giorgio, ywcc, "staff", ["Director of Marketing"])

    gw = _person(c, "p/gwang", "Guiling Wang", {"email": "guiling.wang@njit.edu"})
    _role(c, gw, cs, "faculty", ["Distinguished Professor"])
    _item(c, 4, "research_areas", "Guiling Wang — Research areas", "research areas",
          "p/gwang", areas=["Applied AI", "Transportation", "Blockchain"])
    _item(c, 4, "education", "Guiling Wang — Education", "PhD, University X", "p/gwang")
    _item(c, 4, "publication", "Some paper", "A paper by Guiling Wang", "p/gwang")  # must be excluded

    jw = _person(c, "p/jwang", "Jason Wang", {"email": "jason.wang@njit.edu"})
    _role(c, jw, cs, "faculty", ["Professor"])

    haiphan = _person(c, "p/phan", "Hai Phan")   # no research card
    _role(c, haiphan, cs, "faculty", ["Associate Professor"])
    c.commit()
    yield c
    c.close()


# ── pure ────────────────────────────────────────────────────────────────────────
def test_normalize_name():
    assert entity.normalize_person_name("Halper, Michael") == "Michael Halper"
    assert entity.normalize_person_name("Guiling Wang") == "Guiling Wang"
    assert entity.normalize_person_name("") == ""


# ── resolution ────────────────────────────────────────────────────────────────────
def test_resolve_people_complete_and_normalized(conn):
    names = [h["name"] for h in entity.resolve_people(conn, "michael")]
    assert names == ["Michael Giorgio", "Michael Halper"]   # both, normalized, sorted

def test_resolve_people_word_boundary_not_substring(conn):
    # "lin" must not match "Guiling"; here just assert "wang" doesn't match non-Wangs
    assert [h["name"] for h in entity.resolve_people(conn, "wang")] == ["Guiling Wang", "Jason Wang"]

def test_persons_in_query_full_name_only(conn):
    assert [p["name"] for p in entity.persons_in_query(conn, "guiling wang research")] == ["Guiling Wang"]
    assert entity.persons_in_query(conn, "professor wang") == []     # surname only → no full match

def test_persons_by_lastname(conn):
    assert [p["name"] for p in entity.persons_by_lastname(conn, "wang")] == ["Guiling Wang", "Jason Wang"]
    assert entity.persons_by_lastname(conn, "informatics") == []


# ── role_in_org: exact head match ─────────────────────────────────────────────────
def test_role_in_org_dean_excludes_associate_dean(conn):
    rows = entity.role_in_org(conn, 2, "dean")          # org_id 2 = YWCC
    assert [r[0] for r in rows] == ["Jamie Payton"]     # NOT Brook Wu (Associate Dean)

def test_role_in_org_associate_dean_matches_only_associates(conn):
    rows = entity.role_in_org(conn, 2, "associate dean")
    assert [r[0] for r in rows] == ["Brook Wu"]

def test_role_in_org_absent_role_is_empty(conn):
    # No 'Chair' title exists → empty → caller falls through to RAG (never names a prof)
    assert entity.role_in_org(conn, 3, "chair") == []


# ── research ──────────────────────────────────────────────────────────────────────
def test_research_of_person_prefers_clean_tags(conn):
    rp = entity.research_of_person(conn, "p/gwang")
    # research_of_person now returns the UNION of KB-item areas + researches edges, deduped and
    # in a deterministic casefold order (was: raw KB-item order).
    assert rp["areas"] == sorted(["Applied AI", "Transportation", "Blockchain"], key=str.casefold)
    assert rp["name"] == "Guiling Wang"

def test_research_of_person_empty_when_none(conn):
    rp = entity.research_of_person(conn, "p/phan")
    assert rp["areas"] == [] and rp["statement"] is None


# ── entity card ───────────────────────────────────────────────────────────────────
def test_entity_card_assembles_and_excludes_publications(conn):
    card = entity.entity_card(conn, "p/gwang")
    assert "Guiling Wang" in card
    assert "Distinguished Professor — Computer Science" in card
    assert "guiling.wang@njit.edu" in card
    assert "Transportation" in card
    assert "PhD, University X" in card           # education included
    assert "A paper by Guiling Wang" not in card  # publication EXCLUDED

def test_entity_card_unknown_is_empty(conn):
    assert entity.entity_card(conn, "p/nobody") == ""


# ── router integration ────────────────────────────────────────────────────────────
def _route(conn, q):
    rt = router.route(conn, q)
    return rt.skill if rt else None

def test_router_dean_of_org(conn):
    # "the <role> of <org>" now routes to the unified role-lookup skill (org-scoped).
    assert _route(conn, "who is the dean of ywcc") == "people_by_role"

def test_router_name_enumeration(conn):
    assert _route(conn, "list all the michaels") == "people_by_name"

def test_router_person_research(conn):
    assert _route(conn, "guiling wang research field") == "research_of_person"

def test_router_entity_card_bare_name(conn):
    assert _route(conn, "guiling wang") == "entity_card"
    assert _route(conn, "tell me about guiling wang") == "entity_card"
    # WS3: "email" now dispatches to contact_of_person (the WS1 finding B1 fixes),
    # not the generic entity_card.
    assert _route(conn, "guiling wang email") == "contact_of_person"

def test_router_surname_disambiguation(conn):
    assert _route(conn, "professor wang") == "person_disambig"

def test_router_chair_routes_but_renders_empty(conn):
    rt = router.route(conn, "who is the chair of informatics")
    assert rt.skill == "people_by_role"
    assert SA.format_answer(SA.run(conn, rt)) == ""     # no chair → empty → falls through to RAG

def test_router_hai_phan_research_empty(conn):
    rt = router.route(conn, "what does hai phan research")
    assert rt.skill == "research_of_person"
    assert SA.format_answer(SA.run(conn, rt)) == ""

# ── guards: must NOT route into the entity layer ──────────────────────────────────
def test_router_non_name_two_words_falls_through(conn):
    assert _route(conn, "machine learning") is None
    assert _route(conn, "travel award") is None

def test_router_duties_process_not_routed(conn):
    assert _route(conn, "what does the dean do") is None

def test_router_existing_routes_unchanged(conn):
    assert _route(conn, "who works on transportation") == "people_by_research_area"
    assert _route(conn, "how many faculty work on blockchain") == "count_people_by_research_area"


# ── answer rendering ──────────────────────────────────────────────────────────────
def test_people_by_name_answer_complete(conn):
    rt = router.route(conn, "list all the michaels")
    ans = SA.format_answer(SA.run(conn, rt))
    assert "Michael Giorgio" in ans and "Michael Halper" in ans and "2 person" in ans

def test_disambig_answer_lists_candidates(conn):
    rt = router.route(conn, "professor wang")
    ans = SA.format_answer(SA.run(conn, rt))
    assert "Guiling Wang" in ans and "Jason Wang" in ans and "which one" in ans.lower()

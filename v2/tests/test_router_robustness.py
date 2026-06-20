"""Bug 2 Facet A — surname resolution must not over-fire on long meta sentences,
and must NOT regress real people whose surname is a common word (Young/White/Brown)."""
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
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    hss = ensure_org(c, "hss", "Humanities & Social Sciences", parent_slug="njit", type="department")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="njit", type="department")
    sync_org_nodes(c)
    for key, name, org in [("p/see", "Adam See", hss), ("p/young", "Patrick Young", cs),
                           ("p/white", "Jane White", cs), ("p/oria", "Vincent Oria", cs)]:
        project_appointment(c, person_key=key, name=name, org_id=org, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    c.commit()
    yield c
    c.close()


def test_long_meta_sentence_does_not_resolve_a_surname(conn):
    q = ("I see you used he for vincent base on your instruction everything should be "
         "them no gender explain and note what happened")
    r = route(conn, q)
    # must NOT confidently pull up Adam See (or anyone) from incidental words
    assert r is None or r.skill != "entity_card"


def test_short_common_word_surnames_still_resolve(conn):
    # the stoplist is dropped — real faculty named Young/White must still be found
    assert route(conn, "professor young").skill in ("entity_card", "person_disambig")
    assert route(conn, "who is white").skill in ("entity_card", "person_disambig")
    assert route(conn, "young").skill in ("entity_card", "person_disambig")


def test_short_person_queries_unaffected(conn):
    assert route(conn, "oria email").skill == "entity_card"
    assert route(conn, "vincent oria").skill == "entity_card"
    assert route(conn, "oria info").skill == "entity_card"


# ── Facet B: profile-link queries ──────────────────────────────────────────────
def test_link_queries_route_to_link_of_person(conn):
    r = route(conn, "oria linkedin")
    assert r.skill == "link_of_person" and r.args["field_key"] == "linkedin"
    r = route(conn, "oria scholar")
    assert r.skill == "link_of_person" and r.args["field_key"] == "scholar"
    r = route(conn, "oria website")
    assert r.skill == "link_of_person" and r.args["field_key"] == "website"


def test_link_words_without_a_person_fall_through(conn):
    # person-required gate: no person resolves -> not a link route
    for q in ("what's on the gsa website", "how do I use github"):
        r = route(conn, q)
        assert r is None or r.skill != "link_of_person"

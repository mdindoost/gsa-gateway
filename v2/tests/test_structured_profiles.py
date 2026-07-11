"""External-profile links/metrics surfacing through the structured layer:
- entity_card ("who is X") → deterministic LINKS suffix
- research_of_person ("X research") → deterministic METRICS suffix
- list/roster skills, and people without profiles → no suffix
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
from v2.core.retrieval import router, structured_answer as SA


def _org(conn, oid, name, slug, otype, parent=None):
    conn.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
                 (oid, parent, name, slug, otype))
    conn.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Org',?,?,?,'test')",
                 (f"org:{slug}", name, json.dumps({"org_id": oid})))
    return conn.execute("SELECT id FROM nodes WHERE key=?", (f"org:{slug}",)).fetchone()[0]


def _person(conn, key, name, attrs=None):
    conn.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person',?,?,?,'crawler')",
                 (key, name, json.dumps(attrs or {})))
    return conn.execute("SELECT id FROM nodes WHERE key=?", (key,)).fetchone()[0]


def _role(conn, pid, onode, category, titles):
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,attrs,source) "
                 "VALUES(?,'has_role',?,?,?,'crawler')",
                 (pid, onode, category, json.dumps({"titles": titles})))


def _item(conn, org_id, typ, entity_id, areas=None, content="x"):
    meta = {"entity_id": entity_id}
    if areas is not None:
        meta["areas"] = areas
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
                 "is_active,created_by) VALUES(?,?,?,?,?,1,1,'crawler')",
                 (org_id, typ, f"{entity_id} {typ}", content, json.dumps(meta)))


PROFILES = {
    "email": "ioannis.koutis@njit.edu",
    "profiles": {
        "scholar": {"url": "https://scholar.google.com/k", "citations": 5021,
                    "h_index": 30, "i10_index": 62, "updated_at": "2026-06"},
        "linkedin": {"url": "https://linkedin.com/in/koutis"},
    },
}


@pytest.fixture
def conn():
    c = create_all(":memory:")
    cs = _org(c, 1, "Computer Science", "computer-science", "department")
    k = _person(c, "p/koutis", "Ioannis Koutis", PROFILES)
    _role(c, k, cs, "faculty", ["Associate Professor"])
    _item(c, 1, "research_areas", "p/koutis", areas=["Spectral graph theory", "Algorithms"])
    _item(c, 1, "education", "p/koutis", content="PhD, Carnegie Mellon")
    plain = _person(c, "p/plain", "Jane Plain")          # no profiles
    _role(c, plain, cs, "faculty", ["Professor"])
    _item(c, 1, "education", "p/plain", content="PhD, MIT")
    c.commit()
    yield c
    c.close()


PAPERS = [{"title": "A Big Paper", "year": "2020", "cited_by": 900}]

# Folio person WITH Scholar + captured papers → folio line replaces links; teasers suppressed.
FOLIO_SCHOLAR = {
    "profiles": {
        "facultyfolio": {"url": "https://facultyfolio.github.io/p/gwang.html"},
        "scholar": {"url": "https://scholar.google.com/g", "top_cited": PAPERS, "newest": PAPERS},
        "linkedin": {"url": "https://linkedin.com/in/gwang"},
    },
}
# Folio person WITHOUT Scholar → note must drop the publications/citations claim.
FOLIO_NO_SCHOLAR = {
    "profiles": {"facultyfolio": {"url": "https://facultyfolio.github.io/p/calvin.html"}},
}
# Non-folio person WITH captured papers → teasers still fire (regression guard for suppression).
PAPERS_NO_FOLIO = {
    "profiles": {"scholar": {"url": "https://scholar.google.com/n", "top_cited": PAPERS}},
}


@pytest.fixture
def folio_conn():
    """Isolated fixture for FacultyFolio surfacing — separate from the Koutis fixture so its
    added people can't perturb the surname-resolution tests above."""
    c = create_all(":memory:")
    cs = _org(c, 1, "Computer Science", "computer-science", "department")
    gw = _person(c, "p/gwang", "Guiling Wang", FOLIO_SCHOLAR)
    _role(c, gw, cs, "faculty", ["Professor"])
    _item(c, 1, "education", "p/gwang", content="PhD, UMass")
    jc = _person(c, "p/calvin", "James Calvin", FOLIO_NO_SCHOLAR)
    _role(c, jc, cs, "faculty", ["Professor"])
    _item(c, 1, "education", "p/calvin", content="PhD, Cornell")
    np = _person(c, "p/nora", "Nora Paperperson", PAPERS_NO_FOLIO)
    _role(c, np, cs, "faculty", ["Professor"])
    _item(c, 1, "education", "p/nora", content="PhD, Rutgers")
    c.commit()
    yield c
    c.close()


def _suffix(conn, q):
    rt = router.route(conn, q)
    assert rt is not None, f"expected a structured route for {q!r}"
    result = SA.run(conn, rt)
    return rt.skill, SA.format_answer(result), SA.deterministic_suffix(result)


def test_who_is_appends_links_not_metrics(conn):
    skill, facts, suffix = _suffix(conn, "who is Ioannis Koutis")
    assert skill == "entity_card"
    assert suffix is not None
    assert "[Google Scholar](https://scholar.google.com/k)" in suffix
    assert "[LinkedIn](https://linkedin.com/in/koutis)" in suffix
    assert "citations" not in suffix            # metrics do NOT show on identity


def test_research_appends_metrics_not_links(conn):
    skill, facts, suffix = _suffix(conn, "Ioannis Koutis research")
    assert skill == "research_of_person"
    assert suffix == "Google Scholar: 5,021 citations, h-index 30, i10-index 62 — as of 2026-06"


def test_person_without_profiles_has_no_suffix(conn):
    skill, facts, suffix = _suffix(conn, "who is Jane Plain")
    assert skill == "entity_card"
    assert suffix is None


def test_list_skill_has_no_suffix(conn):
    skill, facts, suffix = _suffix(conn, "who are the faculty in computer science")
    assert suffix is None


@pytest.mark.parametrize("q", [
    "koutis info", "koutis all info", "koutis", "koutis details",
])
def test_surname_attribute_and_info_route_to_entity_card_with_links(conn, q):
    skill, facts, suffix = _suffix(conn, q)
    assert skill == "entity_card"
    assert suffix is not None and "Google Scholar" in suffix and "LinkedIn" in suffix


def test_surname_email_routes_to_contact_of_person(conn):
    # WS3: "email" now dispatches to the dedicated contact_of_person skill (the WS1 finding
    # B1 fixes) instead of the generic entity_card — no links/metrics suffix on this skill.
    skill, facts, suffix = _suffix(conn, "Koutis's email")
    assert skill == "contact_of_person"
    assert suffix is None


def test_bare_nonperson_word_does_not_misroute(conn):
    # a lone non-surname word must NOT hit the entity card
    assert router.route(conn, "funding") is None or router.route(conn, "funding").skill != "entity_card"


def test_surname_only_research_resolves_unambiguous_person(conn):
    # "what does Koutis work on" — surname only, one Koutis → research_of_person + metrics
    skill, facts, suffix = _suffix(conn, "what does Koutis work on")
    assert skill == "research_of_person"
    assert suffix == "Google Scholar: 5,021 citations, h-index 30, i10-index 62 — as of 2026-06"


# ── FacultyFolio single-link surfacing ──────────────────────────────────────────────

def test_folio_person_suffix_is_single_folio_link(folio_conn):
    skill, facts, suffix = _suffix(folio_conn, "who is Guiling Wang")
    assert skill == "entity_card"
    assert suffix == ("📄 [Guiling Wang's FacultyFolio page]"
                      "(https://facultyfolio.github.io/p/gwang.html) — "
                      "all their links, publications & citation stats in one place")
    # scattered links replaced, and the paper teasers are suppressed
    assert "Google Scholar" not in suffix and "LinkedIn" not in suffix
    assert "Most-cited paper" not in suffix and "Newest paper" not in suffix


def test_folio_label_uses_canonical_name_from_surname_query(folio_conn):
    # a bare-surname query still yields the full normalized name, not the query token "wang"
    skill, facts, suffix = _suffix(folio_conn, "who is Wang")
    assert skill == "entity_card"
    assert "Guiling Wang's FacultyFolio page" in suffix


def test_folio_without_scholar_drops_publications_claim(folio_conn):
    skill, facts, suffix = _suffix(folio_conn, "who is James Calvin")
    assert skill == "entity_card"
    assert suffix == ("📄 [James Calvin's FacultyFolio page]"
                      "(https://facultyfolio.github.io/p/calvin.html) — "
                      "all their profile links in one place")


def test_scholar_push_empty_for_folio_person_but_present_otherwise(folio_conn):
    # suppression is meaningful: the same paper data yields teasers for a NON-folio person.
    rt = router.route(folio_conn, "who is Guiling Wang")
    assert SA.run(folio_conn, rt)["scholar_push"] == []
    rt2 = router.route(folio_conn, "who is Nora Paperperson")
    assert SA.run(folio_conn, rt2)["scholar_push"]  # non-empty → teasers still fire without folio

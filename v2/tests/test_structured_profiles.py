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


def test_surname_only_research_resolves_unambiguous_person(conn):
    # "what does Koutis work on" — surname only, one Koutis → research_of_person + metrics
    skill, facts, suffix = _suffix(conn, "what does Koutis work on")
    assert skill == "research_of_person"
    assert suffix == "Google Scholar: 5,021 citations, h-index 30, i10-index 62 — as of 2026-06"

"""TDD — Task 7: skills hook — expand path + `related` verdict (R1, R2).

Wires area_expand.expand_area_llm into the research-area SKILLS: people_by_research_area /
count_people_by_research_area now use the LLM-verified expansion (structural — both call
_research_entities(expand=True), so list==count can never disagree), and
does_person_research_area gains a `related` verdict for someone who holds a verified sibling
tag but not the exact query tag — so it NEVER contradicts the expanded roster with a false 'no'.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
import v2.core.retrieval.skills as sk


def _fx(monkeypatch):
    # Real schema (not a bare table) — _research_entities' exact-match path queries the FTS5
    # index (knowledge_fts), which only exists/stays in sync via create_all's triggers.
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    people = {"e_wu": ["cyber security"], "e_neamtiu": ["system security"], "e_ml": ["machine learning"]}
    for eid, tags in people.items():
        c.execute(
            "INSERT INTO knowledge_items(org_id,type,is_active,title,content,metadata,version,created_by) "
            "VALUES(1,'research_areas',1,?,?,?,1,'crawler')",
            (eid, " ".join(tags), json.dumps({"entity_id": eid, "areas": tags})))
    c.commit()
    # stub expansion: cyber security -> {cyber security, system security}
    monkeypatch.setattr(sk, "_expand_llm",
        lambda conn, area: {"cyber security", "system security"} if "security" in area else set())
    # stub name resolution to identity
    monkeypatch.setattr(sk, "_named_rows", lambda conn, ids: sorted((i.replace("e_", "").title(), i) for i in ids))
    return c


def test_enumerate_expands_but_yesno_related(monkeypatch):
    c = _fx(monkeypatch)
    names = {n for n, _ in sk.people_by_research_area(c, "cyber security", None)}
    assert {"Wu", "Neamtiu"} <= names and "Ml" not in names       # expanded, ML excluded
    assert sk.count_people_by_research_area(c, "cyber security", None) == len(names)
    # yes/no: Neamtiu is exact-NO for 'cyber security' but holds sibling 'system security' -> 'related', never 'no'
    r = sk.does_person_research_area(c, "e_neamtiu", "cyber security", "Neamtiu")
    assert r["answer"] == "related" and r["matched_area"] == "system security"
    # Wu lists it exactly -> yes
    assert sk.does_person_research_area(c, "e_wu", "cyber security", "Wu")["answer"] == "yes"

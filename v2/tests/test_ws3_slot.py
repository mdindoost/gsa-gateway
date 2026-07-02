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
from v2.core.retrieval.slot_extractor import (resolve_and_validate, KG_SKILL_NAMES,
                                              REQUIRED_SLOTS, build_schema)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "gsa", "Graduate Student Association", "njit", type="gsa")
    ensure_org(c, "acm", "ACM Student Chapter", "gsa", type="club")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.commit()
    yield c
    c.close()


def test_registry_has_three_new_skills():
    for s in ("contact_of_person", "title_of_person", "orgs_by_type"):
        assert s in KG_SKILL_NAMES
    assert REQUIRED_SLOTS["contact_of_person"] == ("person",)
    assert REQUIRED_SLOTS["title_of_person"] == ("person",)
    assert REQUIRED_SLOTS["orgs_by_type"] == ("org_type",)


def test_schema_has_org_type_enum():
    props = build_schema()["properties"]["slots"]["properties"]
    assert set(props["org_type"]["enum"]) == {"club", "department", "college"}


def test_resolve_contact(conn):
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Koutis"}, "koutis email")
    assert r.skill == "contact_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_title(conn):
    r = resolve_and_validate(conn, "title_of_person", {"person": "Koutis"}, "koutis position")
    assert r.skill == "title_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_orgs_by_type_club(conn):
    r = resolve_and_validate(conn, "orgs_by_type", {"org_type": "club"}, "list clubs")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club" and r.args["parent_org_id"] is None


def test_resolve_orgs_by_type_bad_type_abstains(conn):
    assert resolve_and_validate(conn, "orgs_by_type", {"org_type": "office"}, "list offices") is None


def test_resolve_orgs_by_type_parent(conn):
    r = resolve_and_validate(conn, "orgs_by_type", {"org_type": "club", "org": "GSA"}, "clubs in gsa")
    pid = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    assert r.args["parent_org_id"] == pid


def test_resolve_contact_ambiguous_person_disambig(conn):
    project_appointment(conn, person_key="d/wang1", name="Guiling Wang",
                        org_id=conn.execute("SELECT id FROM organizations WHERE slug='cs'").fetchone()[0],
                        category="faculty", titles=["Professor"], source_section="manual", source="dashboard")
    project_appointment(conn, person_key="d/wang2", name="Jian Wang",
                        org_id=conn.execute("SELECT id FROM organizations WHERE slug='cs'").fetchone()[0],
                        category="faculty", titles=["Professor"], source_section="manual", source="dashboard")
    conn.commit()
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Wang"}, "wang email")
    assert r.skill == "person_disambig"


def test_contact_resolves_without_identity_cue(conn):
    # B1/B4 skip the entity_card _identity_cued gate — the contact/title cue IS the intent (review MAJOR)
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Koutis"}, "koutis")
    assert r.skill == "contact_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_school_abstains(conn):
    # 'school' is not in the enum → abstain, never mapped to college (design deferral)
    assert resolve_and_validate(conn, "orgs_by_type", {"org_type": "school"}, "list schools") is None


def test_extract_slots_keeps_org_type(conn):
    # END-TO-END through extract_slots (BLOCKER: org_type must survive the slot whitelist). A stub
    # generator returns the JSON Granite would emit; the org_type slot must reach resolve_and_validate.
    from v2.core.retrieval.slot_extractor import extract_slots
    def stub(system, prompt, schema):
        return {"skill": "orgs_by_type", "slots": {"org_type": "club"}, "confidence": 0.9}
    res = extract_slots("what clubs are there", stub)
    assert res.skill == "orgs_by_type"
    assert res.slots.get("org_type") == "club"   # NOT stripped
    r = resolve_and_validate(conn, res.skill, res.slots, "what clubs are there")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"

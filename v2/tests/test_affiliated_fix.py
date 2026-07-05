"""Affiliated-faculty fix — duplicate-home correction + new `affiliated` tier + marked surfacing.

Covers the senior-eng CRITICAL (the rule MUST be scoped to ≥2-faculty-edge people, else it corrupts
single-home faculty with an org-mismatched KB filing, and empties 0-KB graph-only rosters) and the
RAG/Fable marker-survival + suppression + count-aware guard.
Spec: docs/superpowers/specs/2026-07-05-affiliated-faculty-category-design.md
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.retrieval import entity, structured_answer as SA
from scripts import _fix_duplicate_faculty_home as FIX

# org_ids (organizations.id): CS dept=5, MTSM college=25, Informatics=30, ArtDesign=40
_ORGS = [(100, "Computer Science", 5), (101, "Martin Tuchman School of Management (MTSM)", 25),
         (102, "Informatics", 30), (103, "School of Art + Design", 40)]


def _org(conn, node_id, name, org_id):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(?,?,?,'department')",
                 (org_id, name, f"o{org_id}"))
    conn.execute("INSERT INTO nodes(id,type,key,name,attrs,source,is_active) "
                 "VALUES(?,'Org',?,?,?,'crawler',1)",
                 (node_id, f"org/{org_id}", name, json.dumps({"org_id": org_id})))


def _person(conn, node_id, key, name):
    conn.execute("INSERT INTO nodes(id,type,key,name,source,is_active) "
                 "VALUES(?,'Person',?,?,'crawler',1)", (node_id, key, name))


def _role(conn, pid, oid, category, titles=None):
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source,attrs,is_active) "
                 "VALUES(?,'has_role',?,?,'crawler',?,1)",
                 (pid, oid, category, json.dumps({"titles": titles} if titles else {})))


def _kb(conn, org_id, entity_key):
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
                 "is_active,created_by) VALUES(?,'about','x','bio',?,1,1,'crawler')",
                 (org_id, json.dumps({"entity_id": entity_key})))


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    for nid, name, oid in _ORGS:
        _org(c, nid, name, oid)
    c.commit()
    yield c
    c.close()


# ═══════════════ schema ═══════════════
def test_schema_allows_affiliated(conn):
    # the widened CHECK must accept the new category (else the fix UPDATE fails STRICT)
    _person(conn, 200, "p/x", "X")
    conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source,is_active) "
                 "VALUES(200,'has_role',100,'affiliated','crawler',1)")
    conn.commit()  # no IntegrityError => allowed


# ═══════════════ scoped rule ═══════════════
def test_two_home_person_demotes_non_kb_home(conn):
    # Wang-analog: faculty@CS + faculty@MTSM, KB under CS → MTSM demoted, CS kept.
    _person(conn, 200, "p/wang", "Guiling Wang")
    _role(conn, 200, 100, "faculty", ["Distinguished Professor"])   # CS
    _role(conn, 200, 101, "faculty", ["Distinguished Professor"])   # MTSM (stray)
    _role(conn, 200, 102, "joint", ["Distinguished Professor"])     # Data Science-analog (untouched)
    _kb(conn, 5, "p/wang")                                          # KB filed under CS only
    conn.commit()
    changes, skipped = FIX.plan_changes(conn)
    assert skipped == []
    assert len(changes) == 1
    ch = changes[0]
    assert ch["keep_org"] == "Computer Science"
    assert [o for _e, o in ch["demote"]] == ["Martin Tuchman School of Management (MTSM)"]


def test_single_home_kb_org_mismatch_untouched(conn):
    # HCAD split: ONE faculty edge, KB filed under a DIFFERENT org. Scope excludes single-home → 0.
    _person(conn, 201, "p/godel", "Addison Godel")
    _role(conn, 201, 103, "faculty", ["Professor"])                 # Art+Design edge
    _kb(conn, 25, "p/godel")                                        # KB filed under a parent-college org
    conn.commit()
    changes, skipped = FIX.plan_changes(conn)
    assert changes == [] and skipped == []                          # never in scope, never touched


def test_zero_kb_graph_only_skipped(conn):
    # Theater-analog: 2 faculty edges, NO KB items → 0 keeps → guard SKIPS (never demotes both).
    _person(conn, 202, "p/edwards", "Emily Edwards")
    _role(conn, 202, 100, "faculty", ["Professor"])
    _role(conn, 202, 101, "faculty", ["Professor"])
    conn.commit()
    changes, skipped = FIX.plan_changes(conn)
    assert changes == []
    assert len(skipped) == 1 and "0 keeps" in skipped[0]["reason"]


def test_multi_keep_skipped(conn):
    # Both faculty orgs are in KB-home → 2 keeps → guard SKIPS (never picks arbitrarily).
    _person(conn, 203, "p/dual", "Dual Home")
    _role(conn, 203, 100, "faculty", ["Professor"])
    _role(conn, 203, 101, "faculty", ["Professor"])
    _kb(conn, 5, "p/dual")
    _kb(conn, 25, "p/dual")
    conn.commit()
    changes, skipped = FIX.plan_changes(conn)
    assert changes == []
    assert len(skipped) == 1 and "2 keeps" in skipped[0]["reason"]


def test_joint_and_admin_untouched_and_idempotent(conn):
    _person(conn, 200, "p/wang", "Guiling Wang")
    _role(conn, 200, 100, "faculty", ["Prof"])   # CS home
    _role(conn, 200, 101, "faculty", ["Prof"])   # MTSM stray
    _role(conn, 200, 102, "joint", ["Prof"])     # joint
    _kb(conn, 5, "p/wang")
    conn.commit()
    changes, _ = FIX.plan_changes(conn)
    FIX.apply_changes(conn, changes)
    conn.commit()
    # joint + CS untouched; only MTSM now affiliated
    cats = dict(conn.execute(
        "SELECT o.name, e.category FROM edges e JOIN nodes o ON o.id=e.dst_id WHERE e.src_id=200"))
    assert cats["Computer Science"] == "faculty"                                 # home untouched
    assert cats["Martin Tuchman School of Management (MTSM)"] == "affiliated"     # stray relabeled
    assert cats["Informatics"] == "joint"                                        # joint untouched
    # idempotent: re-plan → nothing (person now single-home, out of scope)
    changes2, _ = FIX.plan_changes(conn)
    assert changes2 == []


# ═══════════════ surfacing (marked, suppressed, deterministic) ═══════════════
def test_title_of_person_is_deterministic():
    assert SA.is_deterministic({"skill": "title_of_person"}) is True


def _wang_with_affiliated(conn):
    _person(conn, 200, "p/wang", "Guiling Wang")
    _role(conn, 200, 100, "faculty", ["Distinguished Professor"])       # CS home
    _role(conn, 200, 102, "joint", ["Distinguished Professor"])         # joint
    _role(conn, 200, 101, "affiliated", ["Distinguished Professor"])    # affiliated (already fixed)
    conn.commit()


def test_title_of_person_marks_joint_and_affiliated(conn):
    _wang_with_affiliated(conn)
    orgs = {o for _t, o in entity.title_of_person(conn, "p/wang")["titles"]}
    assert "Computer Science" in orgs                                            # home unmarked
    assert "Martin Tuchman School of Management (MTSM) (affiliated)" in orgs     # affiliated marked
    assert "Informatics (joint appointment)" in orgs                            # joint marked


def test_entity_card_marks_joint_and_affiliated(conn):
    _wang_with_affiliated(conn)
    card = entity.entity_card(conn, "p/wang")
    assert "— Computer Science" in card and "Computer Science (" not in card    # home unmarked
    assert "(affiliated)" in card and "(joint appointment)" in card


def test_marker_suppressed_on_bare_category_title(conn):
    # a title-less affiliated edge falls back to the category label; must NOT render "affiliated (affiliated)"
    _person(conn, 205, "p/bare", "Bare Title")
    _role(conn, 205, 101, "affiliated")            # no titles attr
    conn.commit()
    card = entity.entity_card(conn, "p/bare")
    assert "(affiliated)" not in card              # marker suppressed
    assert "affiliated — Martin Tuchman School of Management (MTSM)" in card

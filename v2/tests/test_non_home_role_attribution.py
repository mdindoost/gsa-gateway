"""Non-home (joint/affiliated) role attribution — spec
docs/superpowers/specs/2026-08-26-non-home-role-attribution-design.md

A title carried on a joint/affiliated has_role edge is the person's HOME-org title, copied onto
their listing card by the page that cross-listed them. It must never answer "who is the <role> of
<that org>". Reported live as: "who is the chair of informatics" → Halper AND Oria (Oria chairs CS).
"""
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
from v2.core.retrieval import entity, structured_answer
from v2.core.retrieval.entity import (NON_HOME_CATEGORIES, _CATEGORY_MARKER, _ROLE_RANK,
                                      people_by_role, role_in_org, _primary_role)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    inf = ensure_org(c, "informatics", "Informatics", "njit", type="department")
    cs = ensure_org(c, "cs", "Computer Science", "njit", type="department")
    hcad = ensure_org(c, "hcad", "Hillier College", "njit", type="college")
    c.commit()
    sync_org_nodes(c)

    def appt(key, name, org, cat, title, section="manual"):
        project_appointment(c, person_key=key, name=name, org_id=org, category=cat,
                            titles=[title], source_section=section, source="dashboard")

    # THE reported bug: Halper chairs Informatics; Oria chairs CS and is JOINT in Informatics,
    # where his card borrows his CS title.
    appt("d/halper", "Halper, Michael", inf, "admin", "Chair", "Chair")
    appt("d/oria", "Oria, Vincent", cs, "faculty", "Chair", "Professors")
    appt("d/oria", "Oria, Vincent", inf, "joint", "Chair", "Joint Appointments")
    # A NULL-category edge (live: 75 of them — Makerspace staff, postdocs) carrying a real role.
    appt("d/bruno", "Bruno, Cailyn", hcad, None, "Director, Center for Community Systems", "Center")
    # A person whose ONLY edge is joint and whose title carries a role head — the recall hole the
    # rev-3 home-aware rule exists to protect (live analogue: Fjermestad, joint-only).
    appt("d/lone", "Lone, Dana", inf, "joint", "Dean", "Joint Appointments")
    # A joint-only person whose title carries NO role head (the live Fjermestad row).
    appt("d/fjer", "Fjermestad, Jerry", inf, "joint", "Professor of MIS", "Joint Appointments")
    c.commit()
    yield c
    c.close()


def _oid(c, slug):
    return c.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()[0]


def _names(rows):
    return sorted(r[0] for r in rows)


# ── 1. the reported bug ──────────────────────────────────────────────────────────────────────────
def test_org_scoped_chair_excludes_joint_appointment(conn):
    assert _names(people_by_role(conn, "chair", _oid(conn, "informatics"))) == ["Michael Halper"]


def test_org_scoped_chair_of_home_org_still_answers(conn):
    assert _names(people_by_role(conn, "chair", _oid(conn, "cs"))) == ["Vincent Oria"]


# ── 2. org-agnostic: dedupe to the HOME edge, person kept exactly once ───────────────────────────
def test_org_agnostic_lists_person_once_via_home_edge(conn):
    rows = [r for r in people_by_role(conn, "chair") if r[0] == "Vincent Oria"]
    assert len(rows) == 1
    assert rows[0][2] == "Computer Science"          # attributed to the HOME org
    assert "(joint appointment)" not in rows[0][2]


# ── 3. NULL-category guard (SQL 3VL: a bare NOT IN would drop these) ─────────────────────────────
def test_null_category_edge_is_kept(conn):
    assert _names(people_by_role(conn, "director")) == ["Cailyn Bruno"]
    assert _names(people_by_role(conn, "director", _oid(conn, "hcad"))) == ["Cailyn Bruno"]


# ── 4/12. every home category is kept — parametrized so a NEW category is covered by construction ─
@pytest.mark.parametrize("cat", sorted(set(_ROLE_RANK) - NON_HOME_CATEGORIES) + [None])
def test_home_categories_are_all_kept(conn, cat):
    org = ensure_org(conn, f"o-{cat}", f"Org {cat}", "njit", type="department")
    conn.commit()
    sync_org_nodes(conn)
    project_appointment(conn, person_key=f"d/p-{cat}", name="Pat Holder", org_id=org,
                        category=cat, titles=["Chair"], source_section="manual", source="dashboard")
    conn.commit()
    assert _names(people_by_role(conn, "chair", org)) == ["Pat Holder"]


# ── 5. empty after filtering → format_answer "" so the caller falls through to RAG ───────────────
def test_empty_after_filter_renders_empty_string(conn):
    inf = _oid(conn, "informatics")
    assert people_by_role(conn, "provost", inf) == []
    assert structured_answer.format_answer(
        {"skill": "people_by_role", "org_name": "Informatics", "role_head": "provost",
         "rows": []}) == ""


# ── 6. role_in_org is a wrapper and inherits the fix ─────────────────────────────────────────────
def test_role_in_org_inherits_the_filter(conn):
    assert [r[0] for r in role_in_org(conn, _oid(conn, "informatics"), "chair")] == ["Michael Halper"]


# ── 11. NON_HOME_CATEGORIES is defined independently of the display marker ───────────────────────
def test_non_home_set_matches_marker_set_today(conn):
    # A TODAY-FACT, not a definition: _CATEGORY_MARKER answers "how do we DISPLAY a non-home
    # appointment", NON_HOME_CATEGORIES answers "which edges may ANSWER a role query". If you are
    # here because you added a display-only marker, do NOT delete this — decide which set you meant.
    assert NON_HOME_CATEGORIES == set(_CATEGORY_MARKER)


# ── 15. THE rev-3 design change: org-agnostic must not lose a joint-ONLY role holder ─────────────
def test_org_agnostic_keeps_joint_only_holder_marked(conn):
    rows = people_by_role(conn, "dean")
    assert _names(rows) == ["Dana Lone"]
    assert rows[0][2] == "Informatics (joint appointment)"   # kept, but never reads as home


def test_org_scoped_still_excludes_joint_only_holder(conn):
    # org-scoped asks "who is the dean OF Informatics" — a borrowed title cannot answer that even
    # when nothing else can. Empty → RAG.
    assert people_by_role(conn, "dean", _oid(conn, "informatics")) == []


# ── 10. the live joint-only person (Fjermestad): never silently DROPPED, never claimed as home ───
def test_joint_only_person_is_kept_marked_not_dropped(conn):
    # Under the rejected rev-1/rev-2 blanket filter this row VANISHED. The home-aware rule keeps it,
    # marked. (Locks the design change — do not "simplify" this back to a blanket filter.)
    rows = people_by_role(conn, "professor of mis")
    assert [(r[0], r[2]) for r in rows] == [("Jerry Fjermestad", "Informatics (joint appointment)")]
    # ...but he still cannot answer an ORG-SCOPED question about Informatics.
    assert people_by_role(conn, "professor of mis", _oid(conn, "informatics")) == []


# ── 13. org-agnostic renderer: the >25 count-hint boundary ───────────────────────────────────────
def test_org_agnostic_count_hint_branch_at_26_rows():
    rows = [(f"Person {i:02d}", "Chair", f"Org {i % 3}", None) for i in range(26)]
    out = structured_answer.format_answer(
        {"skill": "people_by_role", "org_name": None, "role_head": "chair", "rows": rows})
    assert out.startswith("26 people hold a \"chair\" title at NJIT. Narrow it by org")
    assert "Person 00" not in out               # the hint branch names orgs, not people


# ── 14 / Part C. _primary_role must mark a borrowed org (feeds deterministic person_disambig) ────
def test_primary_role_marks_joint_only_person(conn):
    node = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key='d/fjer'").fetchone()[0]
    assert _primary_role(conn, node) == ("Professor of MIS", "Informatics (joint appointment)")


def test_primary_role_leaves_home_appointment_bare(conn):
    node = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key='d/oria'").fetchone()[0]
    assert _primary_role(conn, node) == ("Chair", "Computer Science")


# ── 17. A3 antecedent guard: the result now names ONE person, so a follow-up can resolve ─────────
def test_person_names_of_chair_result_is_single(conn):
    result = {"skill": "people_by_role", "org_name": "Informatics", "role_head": "chair",
              "rows": people_by_role(conn, "chair", _oid(conn, "informatics"))}
    assert structured_answer.person_names_of(result) == ["Michael Halper"]


# ── 18. the intended inconsistency, pinned: title_of_person still shows the marked joint org ─────
def test_title_of_person_still_reports_the_marked_joint_appointment(conn):
    card = entity.title_of_person(conn, "d/oria")
    assert ("Chair", "Informatics (joint appointment)") in card["titles"]
    assert ("Chair", "Computer Science") in card["titles"]
    assert _names(people_by_role(conn, "chair", _oid(conn, "informatics"))) == ["Michael Halper"]


# ── 7 / Part B. people_in_org marks a borrowed title with the EXACT guarded literal ──────────────
def test_people_in_org_marks_joint_title(conn):
    from v2.core.retrieval import skills
    rows = {n: t for n, t, _e in skills.people_in_org(conn, _oid(conn, "informatics"))}
    assert rows["Halper, Michael"] == "Chair"                       # home appointment: bare
    assert rows["Oria, Vincent"] == "Chair (joint appointment)"     # borrowed title: marked
    assert rows["Fjermestad, Jerry"] == "Professor of MIS (joint appointment)"


def test_people_in_org_marker_is_the_literal_the_compose_guard_greps_for(conn):
    from bot.core.message_handler import _compose_preserves_facts
    from v2.core.retrieval import skills
    facts = structured_answer.format_answer(
        {"skill": "people_in_org", "org_name": "Informatics", "area": None,
         "rows": skills.people_in_org(conn, _oid(conn, "informatics"))})
    assert "(joint appointment)" in facts
    assert _compose_preserves_facts(facts, facts) is True
    assert _compose_preserves_facts(facts, facts.replace(" (joint appointment)", "")) is False


# ── 16 / Part D. multi-row people_by_role Facts are now guarded against compose dropping a name ──
def test_compose_guard_covers_people_by_role_multirow_facts():
    from bot.core.message_handler import _compose_preserves_facts
    facts = ('2 hold a "chair" title in Newark College of Engineering: '
             'Lisa Axe — Chair (Chemical & Materials Engineering); '
             'Zhiming Ji — Chair (Mechanical & Industrial Engineering).')
    assert _compose_preserves_facts(facts, facts) is True
    dropped = '2 hold a "chair" title in Newark College of Engineering: Lisa Axe — Chair.'
    assert _compose_preserves_facts(facts, dropped) is False

"""Interim / Acting role-title qualifier — the shared head matcher.

Spec: docs/superpowers/specs/2026-08-15-interim-acting-title-qualifier-delta.md

Bug this pins: NJIT moved to interim leadership (2026-08). With "Interim President" stored,
"who is the president of NJIT" answered "I don't have officer information for Office of the
President" — a false-abstain on a fact we hold. With plain "President" it answered correctly.
"""
from __future__ import annotations

import pytest

from v2.core.retrieval.entity import ROLE_QUALIFIER_RE, title_head_matches


# ── G1/G2: qualifiers match ──────────────────────────────────────────────────
@pytest.mark.parametrize("title,head", [
    ("Interim President", "president"),
    ("Acting Dean", "dean"),
    ("Acting Dean in the Newark College of Engineering", "dean"),
    ("Interim Provost and Executive Vice President for Academic Affairs", "provost"),
    ("Department Chair", "chair"),          # pre-existing scope word, unchanged
    ("Interim Chair", "chair"),             # pre-existing, unchanged
    ("Dean, Newark College of Engineering", "dean"),   # bare head still matches
])
def test_qualified_titles_match_the_role(title, head):
    assert title_head_matches(title, head)


def test_multi_token_qualifier_is_stripped_repeatedly():
    """A single-token strip MISSES this — the reason ROLE_QUALIFIER_RE ends in `+`."""
    assert title_head_matches("Acting Interim Dean", "dean")
    assert title_head_matches("Interim Acting Dean", "dean")


# ── G3: rank modifiers must STILL not match (the anti-regression guard) ──────
@pytest.mark.parametrize("title,head", [
    ("Vice President", "president"),
    ("Associate Dean", "dean"),
    ("Assistant Dean", "dean"),
    ("Deputy Dean", "dean"),
    ("Vice Provost for Graduate Studies", "provost"),
    ("Associate Provost", "provost"),
    ("Associate Chair", "chair"),
    # The ordering property that makes the repeating strip SAFE: a rank word terminates it.
    ("Acting Associate Dean", "dean"),
    ("Interim Vice President", "president"),
])
def test_rank_modifiers_do_not_match(title, head):
    assert not title_head_matches(title, head)


def test_apostrophe_guard():
    """`(?!')` — now applied on BOTH sites (people_by_role previously lacked it)."""
    assert not title_head_matches("President's Advisory Council Member", "president")
    assert not title_head_matches("Dean's Office Coordinator", "dean")


# ── compound titles ──────────────────────────────────────────────────────────
def test_compound_title_segments():
    t = "Senior Vice President of Student Affairs and Dean of Students"
    assert title_head_matches(t, "dean of students")
    assert not title_head_matches(t, "president")   # 'Senior Vice President' is a rank


def test_interim_provost_compound_does_not_leak_to_vice_president():
    """Kam's real title. 'provost' must match segment 1; 'vice president' must NOT match
    segment 2 (Executive Vice President is a rank-modified role)."""
    t = "Interim Provost and Executive Vice President for Academic Affairs"
    assert title_head_matches(t, "provost")
    assert not title_head_matches(t, "vice president")


# ── support-staff lead (finding #4: qualifier-sensitive third site) ──────────
def test_support_lead_blocks_a_trailing_role():
    assert not title_head_matches("Executive Assistant, Dean of Students", "dean of students")


def test_qualified_support_lead_also_blocks():
    """Live false positive before this change: the support-lead test ran on the RAW lead, so an
    'Acting Executive Assistant' slipped past and the person was returned as the dean."""
    assert not title_head_matches(
        "Acting Executive Assistant, Dean of Students", "dean of students")


# ── negative fixture: 'Acting' as a DISCIPLINE word (HCAD/theater is live-crawled) ──
def test_acting_as_a_discipline_is_not_a_qualifier_false_positive():
    assert not title_head_matches("Professor of Acting", "dean")
    assert title_head_matches("Professor of Acting", "professor of acting")
    # 'Acting Program Coordinator' DOES match 'coordinator' after the strip. That is a known,
    # accepted consequence: the strip cannot tell capacity-"Acting" from discipline-"Acting".
    # Pinned so the behavior is a decision on record, not a surprise.
    assert title_head_matches("Acting Program Coordinator", "program coordinator")


def test_qualifier_regex_is_anchored_and_repeating():
    assert ROLE_QUALIFIER_RE.sub("", "Acting Interim Dean") == "Dean"
    assert ROLE_QUALIFIER_RE.sub("", "Dean Acting") == "Dean Acting"   # anchored, not global
    assert ROLE_QUALIFIER_RE.sub("", "Associate Dean") == "Associate Dean"


# ── E2E: routing + answering with interim/acting titles ─────────────────────
# Fixture mirrors v2/tests/test_router_org_role_collision.py so the org-52 collision path is
# exercised exactly as production does it.
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all  # noqa: E402
from v2.core.retrieval import structured_answer  # noqa: E402
from v2.core.retrieval.entity import people_by_role  # noqa: E402
from v2.core.retrieval.router import route  # noqa: E402


def _org(c, oid, name, slug, typ, parent=None):
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type,metadata) "
              "VALUES(?,?,?,?,?,'{}')", (oid, parent, name, slug, typ))
    c.execute("INSERT INTO nodes(type,key,name,attrs,source,ontology_version,is_active,"
              "created_at,updated_at) VALUES('Org',?,?,?,'test',1,1,'','')",
              (f"org:{slug}", name, json.dumps({"org_id": oid})))
    return c.execute("SELECT id FROM nodes WHERE key=?", (f"org:{slug}",)).fetchone()[0]


def _person(c, name, key, active=1):
    c.execute("INSERT INTO nodes(type,key,name,attrs,source,ontology_version,is_active,"
              "created_at,updated_at) VALUES('Person',?,?, '{}', 'test',1,?,'','')",
              (key, name, active))
    return c.execute("SELECT id FROM nodes WHERE key=?", (key,)).fetchone()[0]


def _role(c, pid, onode, category, titles):
    c.execute("INSERT INTO edges(src_id,type,dst_id,category,attrs,source,ontology_version,"
              "is_active,created_at,updated_at) VALUES(?, 'has_role', ?, ?, ?, 'test',1,1,'','')",
              (pid, onode, category, json.dumps({"titles": titles})))


@pytest.fixture()
def interim_conn():
    c = create_all(":memory:")
    njit = _org(c, 1, "NJIT", "njit", "university")
    nce = _org(c, 3, "Newark College of Engineering", "nce", "college", parent=1)
    _org(c, 52, "Office of the President", "president", "office", parent=1)
    _role(c, _person(c, "John Pelesko", "p/pelesko"), njit, "admin", ["Interim President"])
    _role(c, _person(c, "Matthew Bandelt", "p/bandelt"), nce, "admin", ["Acting Dean"])
    c.commit()
    return c


def test_e2e_interim_president_is_answered_not_deflected(interim_conn):
    """THE regression: previously resolved to org 52 and replied 'I don't have officer
    information for Office of the President'."""
    r = route(interim_conn, "who is the president of NJIT")
    assert r is not None and r.args.get("org_id") == 1, "collision swap must reach NJIT, not org 52"
    answer = structured_answer.format_answer(structured_answer.run(interim_conn, r))
    assert "Pelesko" in answer


def test_e2e_interim_qualifier_is_preserved_verbatim_in_the_answer(interim_conn):
    """G5: 'Interim' is load-bearing — the structured answer must carry it (the same reason
    title_of_person is deterministic for the (affiliated)/(joint appointment) markers)."""
    r = route(interim_conn, "who is the president of NJIT")
    answer = structured_answer.format_answer(structured_answer.run(interim_conn, r))
    assert "Interim President" in answer, f"qualifier dropped: {answer!r}"


def test_e2e_acting_dean_is_found_by_role(interim_conn):
    rows = people_by_role(interim_conn, "dean", org_id=3)
    assert [r[0] for r in rows] == ["Matthew Bandelt"]
    assert "Acting Dean" in rows[0][1]


def test_org_answers_title_requires_an_ACTIVE_person():
    """A departed person's still-live admin edge must not pass gate 3 — officers_in_org filters
    p.is_active, so passing the gate on an inactive person recreates the dead-end."""
    from v2.core.retrieval.router import _org_answers_title
    c = create_all(":memory:")
    njit = _org(c, 1, "NJIT", "njit", "university")
    _role(c, _person(c, "Departed Prez", "p/gone", active=0), njit, "admin", ["Interim President"])
    c.commit()
    assert _org_answers_title(c, 1, "president") is False

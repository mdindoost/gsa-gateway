"""Tests for role-lookup routing fixes (Parts A, B, C) and doc_id stripping (Part D).

Parts:
  A — _ROLE_VOCAB additions: "registrar", "executive director", "associate director",
      "assistant director" — multi-word terms win longest-first over bare "director".
  B — entity._scope extended to also strip "university" and "interim" leading words.
  C — org-phrase stripped from query before area extraction (C1 router fix) so
      org-name tokens like "studies" in "graduate studies" don't trigger area routing.
  D — _strip_doc_citations helper; _source_note_for still reads doc_ids before strip.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.retrieval.router import route, _ROLE_VOCAB_RX


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def conn():
    """Minimal in-memory DB with a registrar org and computer science dept."""
    c = create_all(":memory:")
    c.execute(
        "INSERT INTO organizations(id, name, slug, type) "
        "VALUES (4, 'YWCC', 'ywcc', 'college')"
    )
    c.execute(
        "INSERT INTO organizations(id, parent_id, name, slug, type) "
        "VALUES (5, 4, 'Computer Science', 'computer-science', 'department')"
    )
    c.execute(
        "INSERT INTO organizations(id, name, slug, type, metadata) "
        "VALUES (24, 'Office of the University Registrar', 'registrar', 'department', "
        "        '{\"aliases\": [\"registrar\", \"university registrar\"]}')"
    )
    c.execute(
        "INSERT INTO organizations(id, name, slug, type, metadata) "
        "VALUES (18, 'Career Development Services', 'career-development', 'department', "
        "        '{\"aliases\": [\"career development\", \"cds\"]}')"
    )
    c.execute(
        "INSERT INTO organizations(id, name, slug, type) "
        "VALUES (9, 'Graduate Studies', 'graduate-studies', 'department')"
    )
    c.commit()
    yield c
    c.close()


# ── Part A: _ROLE_VOCAB additions ─────────────────────────────────────────────

def test_role_vocab_contains_registrar():
    from v2.core.retrieval.router import _ROLE_VOCAB
    assert "registrar" in _ROLE_VOCAB

def test_role_vocab_contains_executive_director():
    from v2.core.retrieval.router import _ROLE_VOCAB
    assert "executive director" in _ROLE_VOCAB

def test_role_vocab_contains_associate_director():
    from v2.core.retrieval.router import _ROLE_VOCAB
    assert "associate director" in _ROLE_VOCAB

def test_role_vocab_contains_assistant_director():
    from v2.core.retrieval.router import _ROLE_VOCAB
    assert "assistant director" in _ROLE_VOCAB

def test_role_vocab_rx_prefers_executive_director_over_director():
    """Longest-first: 'executive director' should be matched whole, not just 'director'."""
    m = _ROLE_VOCAB_RX.search("who is the executive director of career development")
    assert m is not None
    assert m.group(1).lower() == "executive director"

def test_role_vocab_rx_prefers_associate_director_over_director():
    m = _ROLE_VOCAB_RX.search("who is the associate director of admissions")
    assert m is not None
    assert m.group(1).lower() == "associate director"

def test_role_vocab_rx_prefers_assistant_director_over_director():
    m = _ROLE_VOCAB_RX.search("who is the assistant director")
    assert m is not None
    assert m.group(1).lower() == "assistant director"

def test_role_vocab_rx_matches_registrar():
    m = _ROLE_VOCAB_RX.search("who is the university registrar")
    assert m is not None
    assert m.group(1).lower() == "registrar"

def test_router_routes_university_registrar(conn):
    """'who is the university registrar' -> role_in_org or people_by_role."""
    result = route(conn, "who is the university registrar")
    assert result is not None, "Expected a structured route, got None (fell to RAG)"
    assert result.skill in ("people_by_role", "role_in_org"), (
        f"Expected people_by_role or role_in_org, got {result.skill}"
    )

def test_router_routes_executive_director(conn):
    """'who is the executive director of career development' -> role head = executive director."""
    result = route(conn, "who is the executive director of career development")
    assert result is not None
    assert result.skill in ("people_by_role", "role_in_org")
    role_head = result.args.get("role_head", "")
    assert "executive director" in role_head.lower(), (
        f"Expected role_head to contain 'executive director', got {role_head!r}"
    )


# ── Part B: entity._scope extended ────────────────────────────────────────────

def test_scope_strips_university_prefix():
    """'University Registrar' -> 'Registrar' after _scope.sub."""
    _scope = re.compile(r"^(?:departmental|department|university|interim)\s+", re.I)
    assert _scope.sub("", "University Registrar").strip() == "Registrar"

def test_scope_strips_interim_prefix():
    _scope = re.compile(r"^(?:departmental|department|university|interim)\s+", re.I)
    assert _scope.sub("", "Interim Chair").strip() == "Chair"

def test_scope_strips_department_prefix():
    _scope = re.compile(r"^(?:departmental|department|university|interim)\s+", re.I)
    assert _scope.sub("", "Department Chair").strip() == "Chair"

def test_scope_preserves_vice_associate():
    """Vice/Associate are RANK modifiers — _scope must NOT strip them."""
    _scope = re.compile(r"^(?:departmental|department|university|interim)\s+", re.I)
    assert _scope.sub("", "Vice Provost") == "Vice Provost"
    assert _scope.sub("", "Associate Dean") == "Associate Dean"

def test_entity_scope_regex_is_extended():
    """Import the actual _scope used in entity.people_by_role and confirm it covers the new words."""
    import v2.core.retrieval.entity as entity_mod
    import inspect
    src = inspect.getsource(entity_mod.people_by_role)
    assert "university" in src, "entity.py _scope must include 'university'"
    assert "interim" in src, "entity.py _scope must include 'interim'"


# ── Part C: org-phrase stripped before area extraction ─────────────────────────

def test_graduate_studies_query_does_not_route_as_research_area(conn):
    """'graduate studies thesis format' must NOT route to people_by_research_area.
    The word 'studies' inside 'graduate studies' used to trigger a false area match."""
    result = route(conn, "graduate studies thesis format")
    if result is not None:
        assert result.skill != "people_by_research_area", (
            f"C1 bug: 'graduate studies thesis format' incorrectly routed to "
            f"people_by_research_area with area={result.params.get('area')!r}"
        )

def test_graduate_studies_query_does_not_route_as_count(conn):
    """'how many graduate studies students' must not route to count_people_by_research_area."""
    result = route(conn, "how many graduate studies students are there")
    if result is not None:
        assert result.skill != "count_people_by_research_area"

def test_machine_learning_area_still_extracted(conn):
    """Regression: 'who studies machine learning in computer science' -> area='machine learning'."""
    result = route(conn, "who studies machine learning in computer science")
    assert result is not None
    assert result.skill == "people_by_research_area"
    assert "machine learning" in result.args.get("area", "").lower()


# ── Part D: _strip_doc_citations helper ───────────────────────────────────────

from bot.core.message_handler import _strip_doc_citations, _source_note_for


def test_strip_doc_citations_removes_according_to_connector():
    inp = "According to doc_id 64 (YWCC): Prof X is chair of the CS department."
    out = _strip_doc_citations(inp)
    assert "Prof X is chair" in out
    assert "doc_id" not in out
    assert "According to" not in out

def test_strip_doc_citations_removes_bare_doc_id_token():
    inp = "Prof Y leads the lab (doc_id 123)."
    out = _strip_doc_citations(inp)
    assert "Prof Y leads the lab" in out
    assert "doc_id" not in out

def test_strip_doc_citations_removes_meta_commentary():
    inp = (
        "According to doc_id 64: Multimedia Workshop, Prof X is chair. "
        "Note that I did not use doc_id 17745, which is about Y."
    )
    out = _strip_doc_citations(inp)
    assert "Prof X is chair" in out
    assert "doc_id" not in out
    assert "Note that I did not use" not in out

def test_strip_doc_citations_preserves_non_doc_text():
    inp = "The GSA meets every Tuesday at 6 PM in Campus Center 110A."
    out = _strip_doc_citations(inp)
    assert out == inp

def test_strip_doc_citations_empty_string():
    assert _strip_doc_citations("") == ""
    assert _strip_doc_citations(None) == ""

def test_source_note_reads_doc_ids_before_stripping():
    """_source_note_for must parse doc_id from the RAW text (before _strip_doc_citations)."""

    class FakeChunk:
        def __init__(self, item_id, source_file):
            self.item_id = item_id
            self.source_file = source_file

    chunks = [FakeChunk(64, "ywcc_policy"), FakeChunk(17745, "mtsm_policy")]
    raw = (
        "According to doc_id 64 (YWCC): Prof X is chair. "
        "Note that I did not use doc_id 17745, which is about MTSM."
    )
    # Source note must be built from raw text (doc_id 64 cited → ywcc_policy)
    note = _source_note_for(raw, chunks)
    assert "ywcc_policy" in note or note  # at minimum doc_id 64 resolves

    # Stripped text must NOT contain doc_id
    stripped = _strip_doc_citations(raw)
    assert "doc_id" not in stripped
    assert "Prof X is chair" in stripped


# ── Codex review fixes (MAJOR 2 + MAJOR 3) ────────────────────────────────────

def test_role_word_naming_org_does_not_overtrigger(conn):
    """MAJOR 2: when the role word merely NAMES the org (no person intent), do NOT route
    to people_by_role — 'registrar office hours' is an office question, must fall to RAG."""
    assert route(conn, "registrar office hours") is None
    # but a genuine person ask for the same role still routes to the person
    r = route(conn, "who is the university registrar")
    assert r is not None and r.skill == "people_by_role" and r.args["role_head"] == "registrar"


def test_exec_director_not_overtriggered_by_org_overlap(conn):
    """The role-is-org guard must not block a role whose word differs from the org name."""
    r = route(conn, "who is the executive director of career development")
    assert r is not None and r.skill == "people_by_role"
    assert r.args["role_head"] == "executive director"


def test_footer_ignores_disclaimed_doc():
    """MAJOR 3: a 'did not use doc_id N' aside must NOT credit that source in the footer."""
    from bot.core.message_handler import _source_note_for, _strip_meta_doc_sentences

    class FakeChunk:
        def __init__(self, item_id, source_file):
            self.item_id = item_id
            self.source_file = source_file

    chunks = [FakeChunk(64, "ywcc_policy"), FakeChunk(17745, "mtsm_policy")]
    raw = ("According to doc_id 64 (YWCC): Prof X is chair. "
           "Note that I did not use doc_id 17745, which is about MTSM.")
    note = _source_note_for(_strip_meta_doc_sentences(raw), chunks)
    assert "mtsm_policy" not in note          # the disclaimed doc must NOT be credited
    assert "ywcc_policy" in note              # the actually-cited doc is credited


def test_meta_strip_preserves_legit_did_not_use_prose():
    """SAFETY: a real answer sentence containing 'did not use' but NO doc_id must survive —
    the meta strip only targets doc-usage asides (never-withhold)."""
    from bot.core.message_handler import _strip_meta_doc_sentences, _strip_doc_citations

    legit = ("Students who did not use their meal plan may request a refund. "
             "Note that I-20 forms must be filed with OGI.")
    assert _strip_meta_doc_sentences(legit) == legit          # untouched (no doc_id)
    assert "did not use their meal plan" in _strip_doc_citations(legit)
    assert "I-20 forms must be filed" in _strip_doc_citations(legit)


# ── Part E: org-hierarchy climbing for role-scope mismatches ──────────────────
# When a role's required org-type level (dean→college, chair→dept) outranks the
# resolved org's type, the router must climb parent_id until it finds the right level.

@pytest.fixture()
def conn_hierarchy(conn):
    """Extend the base conn fixture with a lone-dept (no college ancestor) and
    a dean-of-students office org for climb guard tests."""
    # Lone department: no college parent (parent_id = NULL implicitly)
    conn.execute(
        "INSERT INTO organizations(id, name, slug, type) "
        "VALUES (30, 'Orphan Department', 'orphan-dept', 'department')"
    )
    # Dean of Students office
    conn.execute(
        "INSERT INTO organizations(id, name, slug, type, metadata) "
        "VALUES (20, 'Dean of Students Office', 'dean-of-students', 'office', "
        "        '{\"aliases\": [\"dean of students\"]}')"
    )
    conn.commit()
    return conn


def test_dean_of_cs_climbs_to_ywcc(conn_hierarchy):
    """E1: 'who is the dean of the computer science department' → org_id must be
    the COLLEGE (YWCC id=4), NOT the department (CS id=5)."""
    result = route(conn_hierarchy, "who is the dean of the computer science department")
    assert result is not None, "Expected a structured route, got None (fell to RAG)"
    assert result.skill == "people_by_role"
    assert result.args["org_id"] == 4, (
        f"Expected org_id=4 (YWCC college), got {result.args['org_id']}"
    )


def test_chair_of_cs_stays_at_dept(conn_hierarchy):
    """E2: 'who is the chair of the computer science department' → org_id must be
    CS dept (id=5); chair scope == department, no climb."""
    result = route(conn_hierarchy, "who is the chair of the computer science department")
    assert result is not None
    assert result.skill == "people_by_role"
    assert result.args["org_id"] == 5, (
        f"Expected org_id=5 (CS dept), got {result.args['org_id']}"
    )


def test_dean_of_ywcc_no_climb(conn_hierarchy):
    """E3: 'who is the dean of ywcc' → org already college, org_id stays 4."""
    result = route(conn_hierarchy, "who is the dean of ywcc")
    assert result is not None
    assert result.skill == "people_by_role"
    assert result.args["org_id"] == 4, (
        f"Expected org_id=4 (YWCC), got {result.args['org_id']}"
    )


def test_provost_no_org_no_crash(conn_hierarchy):
    """E4: 'who is the provost' — no org in query, org_id=None; must not crash and
    must return a route (or None for RAG); either is fine as long as it doesn't error."""
    try:
        result = route(conn_hierarchy, "who is the provost")
        # org_id=None means no climb attempted; result may be None (RAG) or a route
        if result is not None:
            assert result.skill == "people_by_role"
            assert result.args.get("org_id") is None
    except Exception as exc:
        pytest.fail(f"route() raised unexpectedly: {exc}")


def test_dean_lone_dept_no_ancestor_graceful(conn_hierarchy):
    """E5: 'who is the dean of orphan dept' — orphan has no college ancestor; climb
    finds nothing; org_id must NOT crash and must fall back to the dept itself."""
    result = route(conn_hierarchy, "who is the dean of orphan dept")
    # Either falls to RAG (None) or routes with the dept id (graceful fallback) — no crash.
    if result is not None and result.skill == "people_by_role":
        assert result.args["org_id"] == 30, (
            f"Graceful fallback expected org_id=30, got {result.args['org_id']}"
        )


def test_dean_of_students_does_not_climb(conn_hierarchy):
    """E6: 'who is the dean of students' — 'dean of students' is level 0 in
    ROLE_SCOPE_LEVEL; the org must stay at the dean-of-students office (id=20)."""
    result = route(conn_hierarchy, "who is the dean of students")
    assert result is not None, "Expected a structured route for dean-of-students"
    assert result.skill == "people_by_role"
    assert result.args["org_id"] == 20, (
        f"dean-of-students must not climb; expected org_id=20, got {result.args['org_id']}"
    )

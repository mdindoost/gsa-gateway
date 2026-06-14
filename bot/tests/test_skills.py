"""Tests for the structured-retrieval skills (v2/core/retrieval/skills.py).

Built on a real-schema fixture (create_all → FTS triggers fire on insert), so the
word-boundary FTS matching and is_active filtering are exercised for real — including
the "graph must not match graphics" precision case the senior review flagged.
"""

import json

import pytest

from v2.core.database.schema import create_all
from v2.core.retrieval.skills import (
    area_counts,
    areas_in_org,
    count_people_by_research_area,
    expand_area,
    faculty_in_department,
    org_departments,
    org_descendants,
    people_by_area_tag,
    people_by_research_area,
    resolve_org,
)


@pytest.fixture
def conn(tmp_path):
    c = create_all(str(tmp_path / "t.db"))
    for oid, parent, name, slug, typ in [
        (1, None, "New Jersey Institute of Technology", "njit", "university"),
        (4, 1, "Ying Wu College of Computing", "ywcc", "college"),
        (5, 4, "Computer Science", "computer-science", "department"),
        (6, 4, "Data Science", "data-science", "department"),
    ]:
        c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(?,?,?,?,?)",
                  (oid, parent, name, slug, typ))

    def add(org, eid, name, typ, content, active=1):
        title = name if typ == "profile" else f"{name} — {typ}"
        c.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
                  "VALUES(?,?,?,?,?,?)",
                  (org, typ, title, content, json.dumps({"entity_id": eid}), active))

    # CS: Koutis does graph; Shih does graphics (must NOT match 'graph')
    add(5, "p/koutis", "Ioannis Koutis", "profile", "Profile: Ioannis Koutis — Professor")
    add(5, "p/koutis", "Ioannis Koutis", "research_areas", "Research areas: graph algorithms, spectral methods")
    add(5, "p/shih", "Frank Shih", "profile", "Profile: Frank Shih")
    add(5, "p/shih", "Frank Shih", "research_areas", "Research areas: computer graphics and image processing")
    # DS: Bader does graph; Oria does multimedia
    add(6, "p/bader", "David Bader", "profile", "Profile: David Bader")
    add(6, "p/bader", "David Bader", "research_statement", "high performance graph analytics on Arkouda")
    add(6, "p/oria", "Vincent Oria", "profile", "Profile: Vincent Oria")
    add(6, "p/oria", "Vincent Oria", "research_areas", "Research areas: multimedia databases")
    # CS: Phan does LLMs (text says "large language models", never the token "llm");
    # MlOnly does plain machine learning — the 'llm' expansion must NOT pull them in.
    add(5, "p/phan", "Hai Phan", "profile", "Profile: Hai Phan")
    add(5, "p/phan", "Hai Phan", "research_areas",
        "Research areas: large language models, generative AI, differential privacy")
    add(5, "p/mlonly", "Em Ell", "profile", "Profile: Em Ell")
    add(5, "p/mlonly", "Em Ell", "research_areas", "Research areas: machine learning")
    # an INACTIVE stale graph row — must be excluded by is_active
    add(5, "p/ghost", "Ghost Prof", "research_areas", "graph theory ghost", active=0)
    c.commit()
    yield c
    c.close()


def _names(rows):
    return {n for n, _ in rows}


# ── org resolution / structure ────────────────────────────────────────────────

def test_resolve_org_by_name_slug_and_alias(conn):
    assert resolve_org(conn, "Ying Wu College of Computing") == 4
    assert resolve_org(conn, "ywcc") == 4
    assert resolve_org(conn, "YWCC") == 4
    assert resolve_org(conn, "Computer Science") == 5
    assert resolve_org(conn, "CS") == 5
    assert resolve_org(conn, "data-science") == 6
    assert resolve_org(conn, "Astrophysics") is None


def test_org_descendants_includes_root(conn):
    assert org_descendants(conn, 4) == {4, 5, 6}
    assert org_descendants(conn, 5) == {5}


def test_org_departments_lists_children(conn):
    assert org_departments(conn, 4) == ["Computer Science", "Data Science"]


# ── faculty / research ────────────────────────────────────────────────────────

def test_faculty_in_department(conn):
    assert _names(faculty_in_department(conn, 5)) == {
        "Ioannis Koutis", "Frank Shih", "Hai Phan", "Em Ell"}
    assert _names(faculty_in_department(conn, 6)) == {"David Bader", "Vincent Oria"}


def test_people_by_research_area_is_word_boundary_not_substring(conn):
    # 'graph' must match Koutis + Bader, NOT Shih's 'graphics', NOT the inactive ghost.
    names = _names(people_by_research_area(conn, "graph", org_id=4))
    assert names == {"Ioannis Koutis", "David Bader"}
    assert "Frank Shih" not in names
    assert "Ghost Prof" not in names


def test_count_matches_list(conn):
    assert count_people_by_research_area(conn, "graph", org_id=4) == 2


def test_research_area_scoped_to_department(conn):
    # scope to DS only → just Bader
    assert _names(people_by_research_area(conn, "graph", org_id=6)) == {"David Bader"}


def test_unknown_area_returns_empty(conn):
    assert people_by_research_area(conn, "quantum", org_id=4) == []
    assert count_people_by_research_area(conn, "quantum", org_id=4) == 0


# ── area expansion (Phase 2: curated FTS query-expansion) ──────────────────────

def test_expand_area_mapped_includes_self_and_synonyms():
    out = expand_area("llm")
    assert "llm" in out                       # keeps the abbreviation itself
    assert "large language models" in out      # bridges to the words profiles use
    # plural key resolves to the same expansion
    assert set(expand_area("llms")) == set(out)


def test_expand_area_unknown_is_single_phrase_fallback():
    # an unmapped term expands to just itself → identical to Phase-1 exact match
    assert expand_area("graph") == ["graph"]
    assert expand_area("quantum") == ["quantum"]


def test_expand_area_normalizes_case_and_whitespace():
    assert expand_area("  LLM ") == expand_area("llm")
    assert expand_area("NLP") == expand_area("nlp")


def test_llm_query_finds_large_language_faculty(conn):
    # "llm" never appears as a token, but expansion bridges to "large language models"
    names = _names(people_by_research_area(conn, "llm", org_id=4))
    assert "Hai Phan" in names


def test_llm_expansion_does_not_over_match_plain_ml(conn):
    # precision guard: expanding "llm" must NOT pull in machine-learning-only faculty
    names = _names(people_by_research_area(conn, "llm", org_id=4))
    assert "Em Ell" not in names


def test_unmapped_tight_term_unchanged_by_expansion(conn):
    # "graph" has no map entry → behaves exactly as Phase 1 (Koutis + Bader only)
    assert _names(people_by_research_area(conn, "graph", org_id=4)) == {
        "Ioannis Koutis", "David Bader"}


def test_count_matches_list_for_expanded_area(conn):
    n = count_people_by_research_area(conn, "llm", org_id=4)
    assert n == len(people_by_research_area(conn, "llm", org_id=4))


# ── areas_in_org / area_counts facet skills ───────────────────────────────────

def _add_areas(conn, org, eid, name, areas):
    """Insert a research_areas item carrying metadata.areas (the P2.5 facet)."""
    import json as _json
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
        "VALUES(?,?,?,?,?,1)",
        (org, "research_areas", f"{name} — Research areas",
         f"Research areas of {name}: " + "; ".join(areas),
         _json.dumps({"entity_id": eid, "areas": areas})))
    # a matching profile so _display_name resolves the person's name
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
        "VALUES(?,?,?,?,?,1)",
        (org, "profile", name, f"Profile: {name}", _json.dumps({"entity_id": eid})))
    conn.commit()


def test_areas_in_org_is_distinct_casefolded_and_org_scoped(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning", "Graph Theory"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning", "Databases"])
    _add_areas(conn, 6, "p/c", "Prof C", ["Robotics"])  # DS, excluded when scope=CS
    assert areas_in_org(conn, 5) == ["Databases", "Graph Theory", "Machine Learning"]
    assert "Robotics" in areas_in_org(conn, 4)


def test_area_counts_counts_distinct_entities_sorted_desc(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning", "Graph Theory"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning"])
    counts = area_counts(conn, 5)
    assert counts[0] == ("Machine Learning", 2)
    assert ("Graph Theory", 1) in counts
    assert len(counts) == len(areas_in_org(conn, 5))


def test_people_by_area_tag_casefold_and_expansion(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning"])
    _add_areas(conn, 5, "p/b", "Prof B", ["large language models"])
    # exact tag, case-insensitive
    assert _names(people_by_area_tag(conn, "machine learning", org_id=5)) == {"Prof A"}
    # P2 expansion: "ml" -> matches the "Machine Learning" tag
    assert _names(people_by_area_tag(conn, "ml", org_id=5)) == {"Prof A"}
    # "llm" expands to "large language models" -> matches Prof B
    assert _names(people_by_area_tag(conn, "llm", org_id=5)) == {"Prof B"}
    # unmapped, unlisted area -> empty (honest)
    assert people_by_area_tag(conn, "astrophysics", org_id=5) == []


# ── follow-ups: batch name resolution + facet consistency ─────────────────────

def test_display_names_batch_prefers_profile_and_falls_back(conn):
    from v2.core.retrieval.skills import _display_names
    got = _display_names(conn, ["p/koutis", "p/oria", "p/unknown"])
    assert got == {"p/koutis": "Ioannis Koutis", "p/oria": "Vincent Oria",
                   "p/unknown": "unknown"}


def test_display_names_overview_fallback_when_no_profile(conn):
    from v2.core.retrieval.skills import _display_names
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
                 "VALUES(5,'overview','Jane Roe — overview','x',?,1)",
                 (json.dumps({"entity_id": "p/roe"}),))
    conn.commit()
    assert _display_names(conn, ["p/roe"]) == {"p/roe": "Jane Roe"}


def test_areas_in_org_is_exactly_area_counts_names(conn):
    # the two facets must never disagree on the area set (areas_in_org derives from counts)
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning", "Graph Theory"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning"])
    assert areas_in_org(conn, 5) == sorted(
        (a for a, _ in area_counts(conn, 5)), key=str.casefold)


def test_canonical_casing_dedups_case_variants_to_one_entry(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning"])
    areas = areas_in_org(conn, 5)
    # one canonical entry for the case-variant group, deterministically chosen
    assert sum(a.casefold() == "machine learning" for a in areas) == 1

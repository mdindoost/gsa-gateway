"""Surfacing the Scholar capture: pull skills (papers, trend, recent metrics) + push suffix.

Skills tested directly + through run()/format_answer(); router tested via route().
Fixtures model the captured attrs.profiles.scholar shape.
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
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval import entity, router
from v2.core.retrieval.router import Route
from v2.core.retrieval.structured_answer import (
    run, format_answer, is_deterministic, deterministic_suffix)

SCHOLAR = {
    "citations": 2791, "h_index": 26, "i10_index": 35,
    "recent_citations": 1063, "recent_h_index": 19, "recent_i10_index": 22,
    "recent_since_year": 2021,
    "cites_per_year": {"2007": 8, "2010": 62, "2019": 208, "2024": 194, "2025": 251, "2026": 152},
    "top_cited": [
        {"title": "Approaching optimality for SDD", "year": "2010", "venue": "FOCS",
         "cited_by": 390, "url": "https://scholar.google.com/c1"},
        {"title": "A nearly-mlogn solver", "year": "2011", "venue": "STOC",
         "cited_by": 312, "url": "https://scholar.google.com/c2"},
    ],
    "newest": [
        {"title": "Ridge spectral sparsification", "year": "2026", "venue": "arXiv",
         "cited_by": 0, "url": "https://scholar.google.com/n1"},
        {"title": "Hierarchical mamba", "year": "2025", "venue": "ICML",
         "cited_by": 4, "url": "https://scholar.google.com/n2"},
    ],
    "current_year": [
        {"title": "Ridge spectral sparsification", "year": "2026", "venue": "arXiv",
         "cited_by": 0, "url": "https://scholar.google.com/n1"},
    ],
}


def _conn(scholar=SCHOLAR, name="Ioannis Koutis", key="p/k"):
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','department')")
    attrs = {"profiles": {"scholar": scholar}} if scholar is not None else {}
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person',?,?,?,'crawler')",
              (key, name, json.dumps(attrs)))
    c.commit()
    return c


# ── papers_of_person: skill ────────────────────────────────────────────────

def test_papers_most_cited_returns_top_cited():
    r = entity.papers_of_person(_conn(), "p/k", "most_cited")
    assert r["mode"] == "most_cited"
    assert r["papers"][0]["cited_by"] == 390


def test_papers_newest_returns_newest():
    r = entity.papers_of_person(_conn(), "p/k", "newest")
    assert r["papers"][0]["title"] == "Ridge spectral sparsification"


def test_papers_current_year_returns_current():
    r = entity.papers_of_person(_conn(), "p/k", "current_year")
    assert len(r["papers"]) == 1 and r["papers"][0]["year"] == "2026"


def test_papers_empty_when_no_scholar():
    r = entity.papers_of_person(_conn(scholar=None), "p/k", "most_cited")
    assert r["papers"] == []


# ── papers_of_person: render is deterministic ──────────────────────────────

def test_papers_render_most_cited_is_deterministic_and_verbatim():
    res = run(_conn(), Route("papers_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "most_cited", "n": 1}))
    assert is_deterministic(res)                       # never reworded by the LLM
    out = format_answer(res)
    assert "Approaching optimality for SDD" in out and "390" in out
    assert "https://scholar.google.com/c1" in out      # link verbatim


def test_papers_render_top_n_lists():
    res = run(_conn(), Route("papers_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "most_cited", "n": 2}))
    out = format_answer(res)
    assert "Approaching optimality for SDD" in out and "A nearly-mlogn solver" in out


def test_papers_render_current_year_count():
    res = run(_conn(), Route("papers_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "current_year", "n": 10}))
    out = format_answer(res)
    assert "2026" in out and "Ridge spectral sparsification" in out


def test_papers_render_empty_falls_through_to_rag():
    res = run(_conn(scholar=None), Route("papers_of_person",
                                         {"entity_id": "p/k", "name": "Koutis", "mode": "most_cited", "n": 1}))
    assert format_answer(res) == ""                    # "" → _try_structured falls back to RAG


# ── citation_trend_of_person ───────────────────────────────────────────────

# all-time-provable: chart sum ≈ citations (hidden < peak)
_ALLTIME = {"citations": 880,
            "cites_per_year": {"2010": 62, "2019": 208, "2024": 194, "2025": 251, "2026": 152,
                               "2007": 8}}  # sum 875, hidden 5 < 251


def test_trend_year_count():
    res = run(_conn(), Route("citation_trend_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "year", "year": 2019}))
    assert is_deterministic(res)
    assert "208" in format_answer(res) and "2019" in format_answer(res)


def test_trend_year_absent_is_honest():
    res = run(_conn(), Route("citation_trend_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "year", "year": 1990}))
    out = format_answer(res)
    assert "1990" in out and "don't have" in out.lower()


def test_trend_peak_windowed_when_not_provable_all_time():
    # main SCHOLAR fixture: sparse chart (sum 875) << citations 2791 -> NOT all-time
    res = run(_conn(), Route("citation_trend_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "peak", "year": None}))
    out = format_answer(res)
    assert "2025" in out and "251" in out
    assert "since 2007" in out and "all-time" not in out.lower()


def test_trend_peak_all_time_when_provable():
    res = run(_conn(scholar=_ALLTIME, name="Ioannis Koutis"),
              Route("citation_trend_of_person",
                    {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "peak", "year": None}))
    out = format_answer(res)
    assert "2025" in out and "all-time" in out.lower()


def test_trend_growth_describes_direction():
    res = run(_conn(), Route("citation_trend_of_person",
                             {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "growth", "year": None}))
    out = format_answer(res)
    assert "Ioannis Koutis" in out and ("2025" in out or "2024" in out)


def test_trend_empty_no_chart_falls_through():
    res = run(_conn(scholar={"citations": 5}), Route("citation_trend_of_person",
              {"entity_id": "p/k", "name": "Koutis", "mode": "peak", "year": None}))
    assert format_answer(res) == ""


# ── router wiring: papers + trend disambiguation + no-regression ───────────

def _kg():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="njit", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="p/k", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.execute("UPDATE nodes SET attrs=? WHERE key='p/k'",
              (json.dumps({"profiles": {"scholar": SCHOLAR}}),))
    c.commit()
    return c


def test_route_most_cited_paper_named_person():
    rt = router.route(_kg(), "Ioannis Koutis most cited paper")
    assert rt.skill == "papers_of_person" and rt.args["mode"] == "most_cited"
    assert rt.args["entity_id"] == "p/k"


def test_route_newest_paper():
    rt = router.route(_kg(), "Koutis newest paper")
    assert rt.skill == "papers_of_person" and rt.args["mode"] == "newest"


def test_route_papers_this_year():
    rt = router.route(_kg(), "Koutis papers this year")
    assert rt.skill == "papers_of_person" and rt.args["mode"] == "current_year"


def test_route_most_cited_year_is_trend_not_metric():
    rt = router.route(_kg(), "Koutis most cited year")
    assert rt.skill == "citation_trend_of_person" and rt.args["mode"] == "peak"


def test_route_citations_in_year_is_trend():
    rt = router.route(_kg(), "Koutis citations in 2019")
    assert rt.skill == "citation_trend_of_person" and rt.args["mode"] == "year"
    assert rt.args["year"] == 2019


def test_route_growth_is_trend():
    rt = router.route(_kg(), "is Koutis research growing")
    assert rt.skill == "citation_trend_of_person" and rt.args["mode"] == "growth"


def test_route_paper_in_org_honest_decline():
    rt = router.route(_kg(), "most cited paper in cs")
    assert rt.skill == "papers_cross_unsupported"


# no-regression: existing metric/person-ranking routing must be UNCHANGED
def test_noregression_citations_metric():
    rt = router.route(_kg(), "Koutis citations")
    assert rt.skill == "metric_of_person"


def test_noregression_most_cited_professor():
    rt = router.route(_kg(), "most cited professor in cs")
    assert rt.skill == "top_people_by_metric"


# ── push: multi-line deterministic suffix ──────────────────────────────────

def test_suffix_entity_card_composes_links_and_papers():
    res = {"skill": "entity_card", "card": "card text", "links": "🎓 [Scholar](u)",
           "scholar_push": ['Most-cited paper: "X" (2010) — 390 citations. u1',
                            'Newest paper: "Y" (2026). u2']}
    s = deterministic_suffix(res)
    assert "Scholar" in s and "Most-cited paper" in s and "Newest paper" in s


def test_suffix_research_composes_metrics_peak_paper():
    res = {"skill": "research_of_person", "research": {"areas": ["graphs"]},
           "metrics": "Google Scholar: 2,791 citations",
           "scholar_push": ["Most-cited year: 2025 (251, all-time).", 'Most-cited paper: "X".']}
    s = deterministic_suffix(res)
    assert "citations" in s and "Most-cited year" in s and "Most-cited paper" in s


def test_suffix_omits_empty_push_unchanged():
    res = {"skill": "entity_card", "card": "c", "links": "🎓 [Scholar](u)", "scholar_push": []}
    assert deterministic_suffix(res) == "🎓 [Scholar](u)"   # existing behavior preserved


def test_suffix_none_when_no_links_and_no_push():
    res = {"skill": "entity_card", "card": "c", "links": None, "scholar_push": []}
    assert deterministic_suffix(res) is None


def test_run_entity_card_populates_scholar_push():
    res = run(_conn(), Route("entity_card", {"entity_id": "p/k", "name": "Ioannis Koutis"}))
    push = res.get("scholar_push") or []
    assert any("Most-cited paper" in l for l in push)
    assert any("Newest paper" in l for l in push)


def test_run_research_populates_peak_and_paper_push():
    res = run(_conn(), Route("research_of_person", {"entity_id": "p/k", "name": "Ioannis Koutis"}))
    push = res.get("scholar_push") or []
    assert any("Most-cited year" in l for l in push)
    assert any("Most-cited paper" in l for l in push)


# ── review fixes (Codex) ───────────────────────────────────────────────────

# HIGH: the message-handler pre-gate must let the new Scholar pull queries reach router.route()
from bot.core.message_handler import _structured_pregate


@pytest.mark.parametrize("q", [
    "Ioannis Koutis most cited paper",
    "what is Koutis newest publication",
    "is Koutis research growing over the years",
    "Koutis most cited year of all time",
])
def test_pregate_passes_scholar_surfacing_queries(q):
    assert _structured_pregate(q) is True


def test_pregate_still_rejects_long_nonstructured():
    assert _structured_pregate("i went to the store yesterday and bought some warm milk") is False


def test_pregate_short_query_always_passes():
    assert _structured_pregate("Guiling Wang") is True   # <=4 words → entity resolution


# MED: current_year must LIST ALL captured papers, not just n — the count must match the list
def test_papers_current_year_lists_all_not_just_n():
    sch = dict(SCHOLAR)
    sch["current_year"] = [
        {"title": "Paper One", "year": "2026", "venue": "A", "cited_by": 0, "url": "u1"},
        {"title": "Paper Two", "year": "2026", "venue": "B", "cited_by": 1, "url": "u2"},
        {"title": "Paper Three", "year": "2026", "venue": "C", "cited_by": 2, "url": "u3"},
    ]
    res = run(_conn(scholar=sch), Route("papers_of_person",
              {"entity_id": "p/k", "name": "Ioannis Koutis", "mode": "current_year", "n": 1}))
    out = format_answer(res)
    assert "3 paper(s)" in out
    assert "Paper One" in out and "Paper Two" in out and "Paper Three" in out


# LOW: "this year" is more specific than "newest/latest" — current_year must win.
# (Full name so the >4-token surname guard doesn't gate person resolution.)
def test_route_this_year_beats_newest():
    rt = router.route(_kg(), "Ioannis Koutis latest papers this year")
    assert rt.skill == "papers_of_person" and rt.args["mode"] == "current_year"


# LOW: malformed attrs (profiles / scholar not a dict) must degrade to honest-empty, never crash
def _conn_malformed(profiles):
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','department')")
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person','p/k','X',?,'crawler')",
              (json.dumps({"profiles": profiles}),))
    c.commit()
    return c


@pytest.mark.parametrize("profiles", [[1, 2], {"scholar": [1, 2]}, "junk"])
def test_papers_tolerates_nondict_attrs(profiles):
    r = entity.papers_of_person(_conn_malformed(profiles), "p/k", "most_cited")
    assert r["papers"] == []


@pytest.mark.parametrize("profiles", [[1, 2], {"scholar": [1, 2]}, "junk"])
def test_trend_tolerates_nondict_attrs(profiles):
    r = entity.citation_trend_of_person(_conn_malformed(profiles), "p/k")
    assert r["cites_per_year"] == {} and r["peak"] is None

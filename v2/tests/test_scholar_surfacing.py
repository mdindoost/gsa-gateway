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
from v2.core.retrieval import entity
from v2.core.retrieval.router import Route
from v2.core.retrieval.structured_answer import run, format_answer, is_deterministic

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

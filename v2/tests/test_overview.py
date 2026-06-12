"""Grounded overview generation (prompt assembly is pure; LLM call injected)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord, Publication
from v2.core.ingestion.overview import (PUB_TITLE_SAMPLE, build_context,
                                        build_prompt, generate)


def koutis(n_pubs=3):
    return EntityRecord(
        entity_id="e/ikoutis", name="Ioannis Koutis", org="Computer Science",
        titles=["Associate Professor"], research_statement="Spectral graph theory.",
        bio="Koutis works on fast Laplacian solvers.",
        teaching=["CS 675 Machine Learning"],
        publications=[Publication(f'A. B. 201{i}. "Graph paper {i}." Venue.', year=f"201{i}")
                      for i in range(n_pubs)],
    )


def test_context_includes_grounding_facts():
    r = koutis()
    r.awards = ["2012 NSF CAREER award"]
    r.experience = ["Associate Professor, 2018 -"]
    ctx = build_context(r)
    assert "Ioannis Koutis" in ctx
    assert "Spectral graph theory" in ctx
    assert "fast Laplacian solvers" in ctx
    assert "Machine Learning" in ctx
    assert "Graph paper 0" in ctx                 # publication TITLE extracted from citation
    assert "NSF CAREER" in ctx                    # awards fed to the summarizer
    assert "Associate Professor, 2018" in ctx     # experience fed in


def test_context_samples_publication_titles_not_all():
    ctx = build_context(koutis(n_pubs=100))
    # bounds the generation input; says how many total vs shown
    assert f"100 total, showing {PUB_TITLE_SAMPLE}" in ctx
    assert ctx.count("  - ") == PUB_TITLE_SAMPLE


def test_prompt_forbids_invention():
    system, user = build_prompt(koutis())
    assert "ONLY" in system and "invent" in system.lower()
    assert "FACTS about the professor" in user


def test_generate_uses_injected_llm_and_strips_preamble():
    calls = {}
    def stub(system, user):
        calls["system"] = system
        return "Overview: Ioannis Koutis is an Associate Professor who studies graphs."
    out = generate(koutis(), stub)
    assert out == "Ioannis Koutis is an Associate Professor who studies graphs."
    assert calls["system"]                         # the LLM was actually called


def test_generate_empty_when_no_name():
    assert generate(EntityRecord(entity_id="x", name=""), lambda s, u: "x") == ""


def test_overview_decomposes_into_its_own_item():
    rec = koutis()
    rec.overview = "Ioannis Koutis studies spectral graph theory and fast solvers."
    items = {it.type: it for it in decompose(rec)}
    assert "overview" in items
    ov = items["overview"]
    assert ov.content.startswith("Overview of Ioannis Koutis (Computer Science):")
    assert ov.natural_key == "e/ikoutis:overview:main"
    assert ov.metadata["entity_id"] == "e/ikoutis"

"""Folding grounded web facts into an entity: maps to KItems + dedups vs NJIT."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.entity import KItem
from v2.core.ingestion.web_extract import Fact
from v2.core.ingestion.web_merge import facts_to_items, merge

EID = "people.njit.edu/profile/ikoutis"


def test_facts_become_verified_items_with_evidence():
    facts = [Fact("award", "NSF CAREER Award (2012)", "2012 NSF CAREER award",
                  "http://site/cv.html")]
    items = facts_to_items(facts, EID, "Ioannis Koutis (Computer Science)")
    assert len(items) == 1
    it = items[0]
    assert it.type == "award"
    assert it.content == "Award received by Ioannis Koutis (Computer Science): NSF CAREER Award (2012)"
    assert it.source_url == "http://site/cv.html"
    assert it.metadata["verified"] is True
    assert it.metadata["evidence"] == "2012 NSF CAREER award"
    assert it.natural_key.startswith(EID + ":award:")


def test_bio_and_research_area_are_not_taken_from_web():
    facts = [Fact("bio", "some bio", "some bio quote", "u"),
             Fact("research_area", "graphs", "graphs quote", "u"),
             Fact("software", "GraphTool", "GraphTool library", "u")]
    items = facts_to_items(facts, EID, "X")
    assert {i.type for i in items} == {"software"}     # bio/research_area dropped


def test_web_publications_are_not_taken():
    # publications come from NJIT only — a web pub natural_key can't be deduped vs
    # NJIT's (different string), so we don't ingest web pubs at all.
    web = facts_to_items([Fact("publication", "Some Paper Title", "Some Paper Title", "web"),
                          Fact("software", "GraphTool library", "GraphTool, a library", "web")],
                         EID, "X")
    assert {i.type for i in web} == {"software"}


def test_merge_adds_new_web_items():
    njit = [KItem(type="profile", title="x", content="x",
                  natural_key=f"{EID}:profile:main",
                  metadata={"entity_id": EID, "verified": True}, source_url="njit")]
    web = facts_to_items([Fact("experience", "Associate Chair (2022-)", "Associate Chair", "web")],
                         EID, "X")
    out = merge(njit, web)
    assert len(out) == 2
    assert any(i.type == "experience" and i.metadata["verified"] for i in out)

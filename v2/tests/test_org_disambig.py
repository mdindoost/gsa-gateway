"""WS4 Phase 4 — org multi-match CLARIFY (finishes WS2's loud deferral).

When an org is NAMED but fuzzily matches ≥2 distinct orgs ('engineering' → Electrical/Mechanical),
the slot-extractor previously DEAD-ABSTAINED (→ RAG). It now emits an `org_disambig` Route that
surfaces a "which department did you mean?" clarify (mirrors WS2's person_disambig).
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
from v2.core.retrieval import structured_answer
from v2.core.retrieval.router import Route
from v2.core.retrieval.slot_extractor import resolve_and_validate


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    ensure_org(c, "njit", "NJIT", None, type="university")
    ensure_org(c, "ee", "Electrical Engineering", "njit", type="department")
    ensure_org(c, "me", "Mechanical Engineering", "njit", type="department")  # 2nd 'engineering'
    sync_org_nodes(c)
    c.commit()
    yield c
    c.close()


# ── render (pure) ──────────────────────────────────────────────────────────────────────────────
def test_org_disambig_render_lists_candidates():
    result = {"skill": "org_disambig",
              "candidates": [{"name": "Electrical Engineering"}, {"name": "Mechanical Engineering"}]}
    out = structured_answer.format_answer(result)
    assert "which" in out.lower()
    assert "Electrical Engineering" in out and "Mechanical Engineering" in out


def test_org_disambig_run_passthrough(conn):
    route = Route("org_disambig", {"candidates": [{"name": "Electrical Engineering"}]})
    result = structured_answer.run(conn, route)
    assert result["skill"] == "org_disambig"
    assert result["candidates"] == [{"name": "Electrical Engineering"}]


# ── slot-extractor emits org_disambig on an ambiguous org (was a dead abstain) ───────────────────
def test_ambiguous_org_emits_org_disambig(conn):
    r = resolve_and_validate(
        conn, "orgs_by_type", {"org_type": "department", "org": "engineering"},
        "list the departments in engineering")
    assert isinstance(r, Route) and r.skill == "org_disambig"
    names = {c["name"] for c in r.args["candidates"]}
    assert {"Electrical Engineering", "Mechanical Engineering"} <= names


def test_unambiguous_org_still_resolves(conn):
    # a clean exact org must NOT trigger disambig (no behavior change on the happy path)
    r = resolve_and_validate(
        conn, "orgs_by_type", {"org_type": "department", "org": "Electrical Engineering"},
        "list the departments in Electrical Engineering")
    assert r is None or r.skill != "org_disambig"


def test_org_disambig_renders_deterministically():
    # senior review #10: a clarify list must be sent VERBATIM from the KG, never LLM-reworded
    # (rewording risks the model dropping or altering a candidate — defeats the disambiguation).
    assert structured_answer.is_deterministic(
        {"skill": "org_disambig", "candidates": [{"name": "Electrical Engineering"}, {"name": "Mechanical Engineering"}]}
    ) is True

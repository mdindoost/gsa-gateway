"""A15b Commit 2 — validate the determiner-stripped loose-area candidate too.

The loose-verb branch deliberately keeps the determiner ("study the brain" → "the brain"), which
fails is_listed_research_area ("Brain Imaging" ≠ "the brain") even though "brain" IS a listed tag.
Fix: at the validation site, also try the leading-determiner-stripped form. is_listed_research_area
stays the sole guard, so worst case is unchanged (→ RAG / None).
"""
import sqlite3

import pytest

from v2.core.retrieval import router


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect("gsa_gateway.db")
    yield c
    c.close()


def test_which_professors_study_the_brain_routes_to_kg(conn):
    r = router.route(conn, "which professors study the brain")
    assert r is not None and r.skill == "people_by_research_area"
    assert r.args["area"] == "brain"          # determiner stripped so it validates


def test_who_studies_neuroscience_still_routes(conn):
    # unchanged: no determiner, already validated
    r = router.route(conn, "who studies neuroscience")
    assert r is not None and r.skill == "people_by_research_area"
    assert r.args["area"] == "neuroscience"


# ── hard-negatives: determiner-strip must NOT open non-tag facet phrases ─────────
@pytest.mark.parametrize("q", [
    "faculty in the news",
    "faculty in the department",
    "professors in the meeting",
])
def test_facet_phrases_still_fall_to_rag(conn, q):
    # stripped form ("news"/"department"/"meeting") is not a research-area tag → None → RAG
    assert router.route(conn, q) is None


def test_count_intent_with_determiner(conn):
    r = router.route(conn, "how many professors study the brain")
    assert r is not None and r.skill == "count_people_by_research_area"
    assert r.args["area"] == "brain"

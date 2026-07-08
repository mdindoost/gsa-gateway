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
from v2.core.retrieval import router
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    project_appointment(c, person_key="d/oria", name="Vincent Oria", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.commit()
    yield c
    c.close()


def _skill(conn, q):
    r = route(conn, q)
    return r.skill if r else None


# ── F1 HARD GATE: _extract_area must mine NONE of the facet cue words as a research area,
# else the query routes to a research skill BEFORE facet dispatch. ────────────────────────────
_CUE_WORDS = [
    "news", "award", "awards", "honors", "honours", "prize", "recognition", "won", "received",
    "latest", "recent", "announcement", "involved", "involvement", "workshop", "committee",
    "service", "organize", "bio", "biography", "background",
]


@pytest.mark.parametrize("word", _CUE_WORDS)
def test_f1_extract_area_ignores_cue_words(word):
    # A bare person+cue query has no research verb, so _extract_area yields nothing to mine.
    assert router._extract_area(f"koutis {word}", None) is None
    assert router._extract_area(f"{word} of oria", None) is None


# ── The four facet routes ─────────────────────────────────────────────────────────────────────
def test_news_routes_news(conn):
    assert _skill(conn, "Oria news") == "news_of_person"
    assert _skill(conn, "news about Koutis") == "news_of_person"


def test_awards_routes_awards(conn):
    assert _skill(conn, "Koutis awards") == "awards_of_person"
    # verbose WH-phrasing exceeds the surname ≤4-token guard, so the eval uses the FULL name.
    assert _skill(conn, "what awards has Vincent Oria won") == "awards_of_person"


def test_involvement_routes_involvement(conn):
    assert _skill(conn, "what is Vincent Oria involved in") == "involvement_of_person"
    assert _skill(conn, "Koutis workshops") == "involvement_of_person"


def test_bio_routes_bio(conn):
    assert _skill(conn, "tell me more about Vincent Oria") == "bio_of_person"
    assert _skill(conn, "Koutis biography") == "bio_of_person"
    assert _skill(conn, "Oria's background") == "bio_of_person"


# ── F2 SHADOWING: more-specific research/paper branches win over news' recent/latest ───────────
def test_recent_research_shadowed_by_research(conn):
    assert _skill(conn, "Oria recent research") == "research_of_person"


def test_latest_paper_shadowed_by_papers(conn):
    assert _skill(conn, "Koutis's latest paper") == "papers_of_person"


# ── The card contract is UNCHANGED ────────────────────────────────────────────────────────────
def test_who_is_still_entity_card(conn):
    assert _skill(conn, "who is Oria") == "entity_card"


def test_tell_me_about_still_entity_card(conn):
    # "tell me about X" (no "more") stays the card — only "tell me MORE about X" is the bio facet.
    assert _skill(conn, "tell me about Koutis") == "entity_card"


def test_email_still_contact(conn):
    assert _skill(conn, "Oria's email") == "contact_of_person"


# ── No person present → facet cue never fabricates a route ─────────────────────────────────────
def test_facet_cue_without_person_falls_to_rag(conn):
    assert _skill(conn, "latest news about the hackathon") is None


# ── Kill switch: PERSON_FACETS_ENABLED=0 restores prior card behavior ──────────────────────────
def test_gate_off_restores_card(conn, monkeypatch):
    monkeypatch.setenv("PERSON_FACETS_ENABLED", "0")
    assert _skill(conn, "Oria news") is None          # no facet trigger, "news" alone → RAG
    assert _skill(conn, "who is Oria") == "entity_card"
    monkeypatch.setenv("PERSON_FACETS_ENABLED", "1")
    assert _skill(conn, "Oria news") == "news_of_person"

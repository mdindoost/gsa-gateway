"""EntityRecord -> focused KItems (small-to-big, no content caps)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord, KItem, Publication


def koutis(n_pubs=3):
    return EntityRecord(
        entity_id="people.njit.edu/profile/ikoutis",
        name="Ioannis Koutis", org="Computer Science",
        source_url="https://people.njit.edu/profile/ikoutis",
        titles=["Associate Professor"], role="Associate Chair of Graduate Studies",
        research_statement="Spectral graph theory and fast Laplacian solvers.",
        research_areas=["spectral graph theory", "graph sparsification"],
        publications=[Publication(f"Paper {i}", "ICALP", str(2010 + i)) for i in range(n_pubs)],
        awards=["NSF CAREER Award (2012)"],
        teaching=["Machine Learning", "Advanced Algorithms"],
        contact={"email": "ikoutis@njit.edu"}, links={"website": "https://x"},
    )


def _by_type(items):
    out = {}
    for it in items:
        out.setdefault(it.type, []).append(it)
    return out


def test_emits_expected_item_types():
    items = decompose(koutis())
    t = _by_type(items)
    assert set(t) == {"profile", "research_statement", "research_areas",
                      "publication", "award", "teaching"}
    assert len(t["profile"]) == 1           # exactly one anchor


def test_no_publication_cap():
    # the whole point: 25 papers -> 25 items, none dropped or truncated
    items = decompose(koutis(n_pubs=25))
    assert sum(i.type == "publication" for i in items) == 25


def test_every_item_is_name_prefixed_and_entity_linked():
    items = decompose(koutis())
    for it in items:
        assert it.metadata["entity_id"] == "people.njit.edu/profile/ikoutis"
        assert it.metadata["verified"] is True
        assert it.source_url.endswith("/ikoutis")
        # self-contained: the person's name appears in the content
        assert "Koutis" in it.content


def test_typed_context_prefix_on_publication():
    pub = next(i for i in decompose(koutis()) if i.type == "publication")
    assert pub.content.startswith("Publication by Ioannis Koutis (Computer Science):")
    assert "ICALP" in pub.content                    # venue carried in


def test_natural_keys_are_stable_and_distinct():
    items = decompose(koutis())
    keys = [i.natural_key for i in items]
    assert len(keys) == len(set(keys))               # no collisions
    # publication key is deterministic from the title (re-crawl stability)
    again = decompose(koutis())
    assert [i.natural_key for i in again] == keys


def test_empty_sections_are_omitted():
    rec = EntityRecord(entity_id="e1", name="Jane Doe", org="CS",
                       source_url="u", research_statement="", publications=[],
                       awards=[], teaching=[], service=[])
    items = decompose(rec)
    assert [i.type for i in items] == ["profile"]    # only the anchor


def test_bio_and_education_become_their_own_items():
    rec = EntityRecord(
        entity_id="e1", name="Jane Doe", org="CS", source_url="u",
        bio="Jane joined NJIT in 2015 after a postdoc at MIT.",
        education=["PhD, MIT, 2014", "BS, Caltech, 2009"],
    )
    t = _by_type(decompose(rec))
    assert set(t) == {"profile", "about", "education"}
    assert t["about"][0].content.startswith("About Jane Doe (CS):")
    assert "MIT" in t["education"][0].content and "Caltech" in t["education"][0].content


def test_blank_title_publication_skipped():
    rec = EntityRecord(entity_id="e1", name="Jane", org="CS", source_url="u",
                       publications=[Publication("   "), Publication("Real Paper")])
    pubs = [i for i in decompose(rec) if i.type == "publication"]
    assert len(pubs) == 1 and pubs[0].title == "Real Paper"

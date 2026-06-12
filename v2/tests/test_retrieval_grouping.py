"""Entity grouping + parent expansion (R3).

Decomposition turns one professor into many items. A topic query must surface
DIFFERENT faculty (not five papers by one), and each surfaced faculty must arrive
with their profile item for context — even when only a publication matched.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest
import sqlite_vec

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord, Publication
from v2.core.ingestion.reconcile import reconcile_entity
from v2.core.retrieval.retriever import V2Retriever


class StubEmbedder:
    VOCAB = ["graph", "algorithms", "spectral", "vision", "image", "learning",
             "koutis", "smith", "jones", "profile", "publication", "research"]

    def _vec(self, text):
        t = text.lower()
        v = [0.0] * 768
        for i, w in enumerate(self.VOCAB):
            v[i] = float(t.count(w))
        v[700] = 0.05
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else None

    def embed_query(self, text):
        return self._vec(text)

    def embed_document(self, text):
        return self._vec(text)

    def health_check(self):
        return True


def faculty(slug, name, topic, pub_titles):
    return EntityRecord(
        entity_id=f"e/{slug}", name=name, org="Computer Science",
        source_url=f"https://people.njit.edu/profile/{slug}",
        titles=["Professor"], research_statement=f"{topic} research.",
        research_areas=[topic],
        publications=[Publication(t, "ICALP", "2019") for t in pub_titles],
    )


@pytest.fixture()
def retriever():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','custom')")
    stub = StubEmbedder()

    records = [
        faculty("koutis", "Koutis", "graph algorithms",
                ["Fast graph algorithms", "Spectral graph theory",
                 "Graph sparsification", "Graph partitioning algorithms"]),
        faculty("smith", "Smith", "graph algorithms",
                ["Dynamic graph algorithms", "Streaming graph algorithms"]),
        faculty("jones", "Jones", "computer vision",
                ["Image segmentation", "Vision and image learning"]),
    ]
    for rec in records:
        res = reconcile_entity(conn, 1, rec.entity_id, decompose(rec))
        for iid in res.to_embed:
            content = conn.execute(
                "SELECT content FROM knowledge_items WHERE id=?", (iid,)).fetchone()["content"]
            vec = stub.embed_document(content)
            conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES(?,?)",
                         (iid, sqlite_vec.serialize_float32(vec)))
    conn.commit()
    r = V2Retriever(conn, stub)
    yield r
    conn.close()


def test_topic_query_surfaces_multiple_faculty(retriever):
    chunks = retriever.retrieve("graph algorithms", org_id=1, limit=4)
    urls = {c.source_url for c in chunks}
    # both graph-algorithms faculty represented, not just one professor's papers
    assert any("koutis" in u for u in urls)
    assert any("smith" in u for u in urls)


def test_matched_faculty_get_their_profile_expanded(retriever):
    chunks = retriever.retrieve("graph algorithms", org_id=1, limit=3)
    # every distinct entity in the result has a profile chunk present
    by_entity: dict[str, set[str]] = {}
    for c in chunks:
        by_entity.setdefault(c.source_url, set()).add(c.type)
    for url, types in by_entity.items():
        assert "profile" in types, f"{url} missing profile context: {types}"
    # at least one profile arrived via expansion (not a primary hit)
    assert any(c.type == "profile" and c.source == "expanded" for c in chunks)


def test_source_url_and_verified_carried(retriever):
    chunks = retriever.retrieve("graph algorithms", org_id=1, limit=3)
    assert all(c.source_url and c.source_url.startswith("https://") for c in chunks)
    assert all(c.verified is True for c in chunks)


def test_item_type_filter_suppresses_profile_expansion(retriever):
    # caller restricted to publications -> a profile must NOT be injected
    chunks = retriever.retrieve("graph algorithms", org_id=1,
                                item_types=["publication"], limit=3)
    assert chunks  # still finds publications
    assert all(c.type == "publication" for c in chunks)
    assert not any(c.source == "expanded" for c in chunks)


def test_expansion_does_not_cross_orgs_on_shared_entity_id():
    # two orgs ingest the SAME entity_id; expansion must stay within the queried org
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'A','a','custom')")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(2,'B','b','custom')")
    stub = StubEmbedder()
    rec = faculty("koutis", "Koutis", "graph algorithms", ["Fast graph algorithms"])
    for org in (1, 2):
        res = reconcile_entity(conn, org, rec.entity_id, decompose(rec))
        for iid in res.to_embed:
            content = conn.execute("SELECT content FROM knowledge_items WHERE id=?",
                                   (iid,)).fetchone()["content"]
            conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES(?,?)",
                         (iid, sqlite_vec.serialize_float32(stub.embed_document(content))))
    conn.commit()
    r = V2Retriever(conn, stub)
    chunks = r.retrieve("graph algorithms", org_id=1, limit=3)
    org1_ids = {row["id"] for row in conn.execute(
        "SELECT id FROM knowledge_items WHERE org_id=1")}
    assert chunks
    assert all(c.item_id in org1_ids for c in chunks)  # nothing leaked from org 2
    conn.close()


def test_group_by_entity_can_be_disabled(retriever):
    grouped = retriever.retrieve("graph algorithms", org_id=1, limit=3, group_by_entity=True)
    flat = retriever.retrieve("graph algorithms", org_id=1, limit=3, group_by_entity=False)
    assert len(flat) == 3                       # strict top-k, no expansion
    assert not any(c.source == "expanded" for c in flat)
    assert any(c.source == "expanded" for c in grouped)

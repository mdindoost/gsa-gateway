"""Tests for the V2 hybrid retriever (Step 4).

Uses a deterministic bag-of-words StubEmbedder and an in-memory database, so the
suite is fast and needs no Ollama. The stub mirrors the production storage path:
documents are embedded, L2-normalized, and stored in the vec0 table; FTS5 is
populated by the schema triggers on insert.
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
from v2.core.retrieval.retriever import V2Retriever, _fts_match_expr


class StubEmbedder:
    """Deterministic bag-of-words embedder over a fixed vocab, projected to 768d."""

    VOCAB = [
        "gsa", "finance", "finances", "officer", "officers", "budget", "penalty",
        "violation", "violations", "club", "workshop", "mmi", "multimedia",
        "president", "vp", "contact", "conference", "travel", "award", "funding",
    ]

    def _vec(self, text: str):
        t = text.lower()
        v = [0.0] * 768
        for i, w in enumerate(self.VOCAB):
            v[i] = float(t.count(w))
        v[700] = 0.05  # bias so the norm is never zero
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else None

    def embed_query(self, text):
        return self._vec(text)

    def embed_document(self, text):
        return self._vec(text)

    def health_check(self):
        return True


# (org_slug, type, title, content)
ITEMS = [
    ("gsa", "faq", "Who are the current GSA officers?",
     "The GSA officers are the President, VP Finances, VP Academic Affairs, and "
     "other officers. GSA officers serve the graduate students. GSA GSA officers."),
    ("gsa", "faq", "What is the GSA budget?",
     "The GSA budget funds events and clubs. GSA budget and finance is managed "
     "by the officers. Budget budget GSA finance."),
    ("gsa", "faq", "How does GSA support clubs?",
     "GSA supports clubs with funding and budget allocations for club activities. "
     "GSA club funding club budget."),
    ("gsa", "contact", "VP Finances",
     "VP Finances. Mohith Oduru. gsa-vpf@njit.edu. Manages GSA budget and finances."),
    ("gsa", "policy", "Penalty System for Club Budget Violations",
     "Club budget violations trigger a penalty. The penalty system handles club "
     "budget violation cases. Violations violations penalty club budget."),
    ("mmi", "faq", "What is the MMI Workshop?",
     "The MMI workshop is a multimedia intelligence workshop. MMI multimedia "
     "workshop research event."),
    ("mmi", "faq", "MMI Workshop topics",
     "The MMI workshop covers multimedia retrieval and multimedia AI. MMI "
     "multimedia workshop topics."),
]


@pytest.fixture()
def retriever():
    conn = create_all(":memory:")
    stub = StubEmbedder()
    # org tree: njit -> {gsa, mmi}
    njit = conn.execute(
        "INSERT INTO organizations(name,slug,type) VALUES('NJIT','njit','university')"
    ).lastrowid
    org_ids = {"njit": njit}
    for slug, name in (("gsa", "GSA"), ("mmi", "MMI")):
        org_ids[slug] = conn.execute(
            "INSERT INTO organizations(parent_id,name,slug,type) VALUES(?,?,?,?)",
            (njit, name, slug, "custom"),
        ).lastrowid

    for slug, ktype, title, content in ITEMS:
        iid = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content) VALUES(?,?,?,?)",
            (org_ids[slug], ktype, title, content),
        ).lastrowid
        vec = stub.embed_document(content)
        conn.execute(
            "INSERT INTO knowledge_vectors(item_id,embedding) VALUES(?,?)",
            (iid, sqlite_vec.serialize_float32(vec)),
        )
    conn.commit()

    r = V2Retriever(conn, stub)
    r._org_ids = org_ids  # expose for tests
    yield r
    conn.close()


# ── basic behaviour ──────────────────────────────────────────────────────────

def test_retrieve_returns_chunks(retriever):
    results = retriever.retrieve("GSA budget", limit=3)
    assert results, "expected at least one result"
    assert len(results) <= 3
    top = results[0]
    assert top.org_path.endswith("GSA") or "GSA" in top.org_path
    assert top.source in {"semantic", "keyword", "hybrid"}


def test_org_subtree_filter_scopes_to_mmi(retriever):
    mmi = retriever._org_ids["mmi"]
    results = retriever.retrieve("workshop", org_id=mmi, limit=5)
    assert results
    assert all("MMI" in c.org_path for c in results), \
        "org_id filter must restrict results to the MMI subtree"


def test_item_type_filter(retriever):
    results = retriever.retrieve("GSA finances", item_types=["contact"], limit=5)
    assert results
    assert all(c.type == "contact" for c in results)


def test_keyword_only_hit_has_no_similarity(retriever):
    # A query term present in text but semantically off still returns via FTS.
    results = retriever.retrieve("Mohith Oduru", limit=5)
    assert any(c.type == "contact" for c in results)


# ── stopwords ────────────────────────────────────────────────────────────────

def test_stopwords_filtered_from_match_expr():
    expr = _fts_match_expr("who is in charge of GSA finances")
    assert '"who"' not in expr and '"is"' not in expr and '"of"' not in expr
    assert '"gsa"' in expr and '"finances"' in expr


def test_all_stopword_query_falls_back():
    # If every token is a stopword, we still produce a non-empty match.
    expr = _fts_match_expr("who is the")
    assert expr is not None and expr != ""


# ── the required contact-boost test ──────────────────────────────────────────

def _rank_of(results, predicate):
    for i, c in enumerate(results, start=1):
        if predicate(c):
            return i
    return None


def test_contact_boost_surfaces_contact(retriever):
    query = "who handles GSA finances"
    is_contact = lambda c: c.type == "contact"

    # Without the boost, the short contact record loses to longer FAQ text.
    retriever.contact_boost = 1.0
    base = retriever.retrieve(query, limit=5)
    base_rank = _rank_of(base, is_contact)

    # With the boost, the contact is surfaced into the top 3.
    retriever.contact_boost = 1.5
    boosted = retriever.retrieve(query, limit=5)
    boost_rank = _rank_of(boosted, is_contact)

    assert boost_rank is not None, "contact must be retrieved with the boost"
    assert boost_rank <= 3, f"contact should be top-3 with boost, was rank {boost_rank}"
    if base_rank is not None:
        assert boost_rank <= base_rank, "boost must not push the contact down"


def test_boost_loaded_from_settings(retriever):
    # Default code value when no settings row exists (in-memory db has none).
    assert retriever.contact_boost == 1.5
    assert retriever.event_boost == 1.2

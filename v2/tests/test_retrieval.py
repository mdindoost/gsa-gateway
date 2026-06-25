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


def _insert(retriever, slug, ktype, title, content):
    iid = retriever.conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content) VALUES(?,?,?,?)",
        (retriever._org_ids[slug], ktype, title, content)).lastrowid
    vec = retriever.embedder.embed_document(content)
    retriever.conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES(?,?)",
                           (iid, sqlite_vec.serialize_float32(vec)))
    retriever.conn.commit()
    return iid


def test_publications_excluded_from_default_corpus(retriever):
    # Publications pollute general answers, so the default corpus drops them.
    _insert(retriever, "gsa", "publication", "A Conference Paper",
            "conference travel award funding conference conference")
    results = retriever.retrieve("conference travel award funding", limit=5)
    assert results
    assert all(c.type != "publication" for c in results), \
        "publications must not appear in the default answer corpus"


def test_publications_returned_when_exclusion_overridden(retriever):
    # exclude_types=[] searches everything (e.g. a publications-intent route).
    _insert(retriever, "gsa", "publication", "A Conference Paper",
            "conference travel award funding conference conference")
    results = retriever.retrieve("conference travel award funding", limit=5, exclude_types=[])
    assert any(c.type == "publication" for c in results), \
        "an explicit exclude_types=[] must let publications through"


def test_exclude_types_loaded_from_settings(retriever):
    retriever.conn.execute("INSERT INTO settings(org_id,key,value) VALUES(?,'retriever.exclude_types','faq')",
                           (retriever._org_ids["njit"],))
    retriever.conn.commit()
    from v2.core.retrieval.retriever import V2Retriever
    r2 = V2Retriever(retriever.conn, retriever.embedder)
    assert r2.exclude_types == frozenset({"faq"})


def test_keyword_only_hit_has_no_similarity(retriever):
    # A query term present in text but semantically off still returns via FTS.
    results = retriever.retrieve("Mohith Oduru", limit=5)
    assert any(c.type == "contact" for c in results)


# ── stopwords ────────────────────────────────────────────────────────────────

def test_stopwords_filtered_from_match_expr():
    expr = _fts_match_expr("who is in charge of GSA finances")
    assert '"who"' not in expr and '"is"' not in expr and '"of"' not in expr
    assert '"gsa"' in expr and '"finances"' in expr


def test_existential_there_is_a_stopword():
    # "are there robotic labs" must not match every FAQ containing "there".
    expr = _fts_match_expr("are there robotic labs")
    assert '"there"' not in expr
    assert expr == '"robotic" OR "labs"'


def test_all_stopword_query_falls_back():
    # If every token is a stopword, we still produce a non-empty match.
    expr = _fts_match_expr("who is the")
    assert expr is not None and expr != ""


# ── type boost ───────────────────────────────────────────────────────────────

def test_contact_type_is_not_boosted(retriever):
    # The contact boost was removed: a contact record gets the neutral 1.0 factor,
    # same as any non-event type (officers are answered by the structured router now).
    # _boost_for now takes (row_dict, now) — use decay_for directly for unit checks.
    from datetime import datetime, timezone
    from v2.core.retrieval.retriever import decay_for
    now = datetime.now(timezone.utc)
    assert decay_for({"type": "contact", "metadata": {}}, now) == 1.0
    assert decay_for({"type": "faq", "metadata": {}}, now) == 1.0


def test_event_boost_loaded_from_settings(retriever):
    # Default code value when no settings row exists (in-memory db has none).
    # event_boost on the retriever instance still reflects the setting-loaded value;
    # _boost_for now delegates to decay_for (uses the EVENT_BOOST constant).
    from datetime import datetime, timezone
    from v2.core.retrieval.retriever import decay_for
    now = datetime.now(timezone.utc)
    assert retriever.event_boost == 1.2
    assert decay_for({"type": "event_info", "metadata": {}}, now) == 1.2
    assert not hasattr(retriever, "contact_boost")

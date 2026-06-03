"""Tests for the Retriever service (uses mocks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.retriever import MIN_SIMILARITY, TOP_K_FINAL, Retriever, RetrievedChunk


def make_chunk_dict(
    text: str = "sample text",
    similarity: float = 0.8,
    source_file: str = "gsa_faq.md",
    source_type: str = "faq",
    section_title: str = "FAQ Section",
) -> dict:
    return {
        "chunk_id": f"test_{hash(text)}",
        "text": text,
        "source_file": source_file,
        "source_type": source_type,
        "section_title": section_title,
        "similarity": similarity,
        "metadata": {},
    }


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
    return embedder


@pytest.fixture
def mock_vector_store():
    vs = MagicMock()
    vs.query = MagicMock(return_value=[
        make_chunk_dict("Travel award application process", similarity=0.85),
        make_chunk_dict("Club finance penalties", similarity=0.75),
        make_chunk_dict("GSA president contact info", similarity=0.65),
    ])
    # Hybrid retrieval: BM25 index needs all chunks; empty list disables BM25 in tests
    vs.get_all_chunks = MagicMock(return_value=[])
    # Sibling expansion: no siblings in unit tests
    vs.get_sibling_chunks = MagicMock(return_value=[])
    return vs


@pytest.fixture
def retriever(mock_embedder, mock_vector_store):
    return Retriever(embedder=mock_embedder, vector_store=mock_vector_store)


# ── _build_search_query ───────────────────────────────────────────────────────

def test_build_search_query_no_history(retriever):
    query = retriever._build_search_query("what is the travel award?")
    assert "travel award" in query.lower()


def test_build_search_query_empty_history(retriever):
    query = retriever._build_search_query("what is the travel award?", conversation_history=[])
    assert "travel award" in query.lower()


def test_build_search_query_with_history(retriever):
    history = [
        {"role": "user", "content": "tell me about travel awards"},
        {"role": "assistant", "content": "The travel award application requires Chrome River submission"},
    ]
    query = retriever._build_search_query("what documents do I need?", history)
    # Should include context from history
    assert len(query) > len("what documents do I need?")


def test_build_search_query_cleans_mentions(retriever):
    query = retriever._build_search_query("<@123456> what is GSA?")
    assert "123456" not in query
    assert "what is GSA" in query


# ── _rerank ───────────────────────────────────────────────────────────────────

def test_reranker_boosts_keyword_matches(retriever):
    chunks = [
        make_chunk_dict("unrelated text about weather and sunshine", similarity=0.7, source_type="faq"),
        make_chunk_dict("travel award application reimbursement", similarity=0.7, source_type="faq"),
    ]
    ranked = retriever._rerank("travel award", chunks)
    # The chunk with keyword matches should rank higher (same base similarity, keyword bonus tips it)
    assert ranked[0].text == "travel award application reimbursement"


def test_reranker_boosts_section_title_match(retriever):
    chunks = [
        make_chunk_dict("generic info", similarity=0.7, section_title="About GSA", source_type="faq"),
        make_chunk_dict("some penalties info", similarity=0.65, section_title="Club Penalties", source_type="policy"),
    ]
    ranked = retriever._rerank("penalties", chunks)
    assert ranked[0].section_title == "Club Penalties"


def test_reranker_returns_at_most_top_k(retriever):
    chunks = [
        make_chunk_dict(f"text {i}", similarity=0.8 - i * 0.05)
        for i in range(20)
    ]
    ranked = retriever._rerank("test query", chunks)
    assert len(ranked) <= TOP_K_FINAL


def test_reranker_relevance_score_max_1(retriever):
    chunks = [make_chunk_dict("keywords keywords keywords", similarity=0.99, source_type="faq")]
    ranked = retriever._rerank("keywords", chunks)
    for chunk in ranked:
        assert chunk.relevance_score <= 1.0


# ── retrieve ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_returns_chunks(retriever):
    results = await retriever.retrieve("what is the travel award?")
    assert len(results) > 0
    for r in results:
        assert isinstance(r, RetrievedChunk)
        assert r.relevance_score >= 0.0


@pytest.mark.asyncio
async def test_retrieve_returns_empty_on_embed_failure(mock_vector_store):
    bad_embedder = AsyncMock()
    bad_embedder.embed_query = AsyncMock(return_value=None)
    retriever = Retriever(embedder=bad_embedder, vector_store=mock_vector_store)
    results = await retriever.retrieve("any question")
    assert results == []


def _make_vs_with_chunks(chunks):
    vs = MagicMock()
    vs.query = MagicMock(return_value=chunks)
    vs.get_all_chunks = MagicMock(return_value=[])
    vs.get_sibling_chunks = MagicMock(return_value=[])
    return vs


@pytest.mark.asyncio
async def test_min_similarity_filter(mock_embedder):
    vs = _make_vs_with_chunks([
        make_chunk_dict("highly relevant", similarity=0.8),
        make_chunk_dict("barely relevant", similarity=MIN_SIMILARITY - 0.01),
        make_chunk_dict("not relevant", similarity=0.1),
    ])
    retriever = Retriever(embedder=mock_embedder, vector_store=vs)
    results = await retriever.retrieve("test query")
    for r in results:
        assert r.similarity >= MIN_SIMILARITY


@pytest.mark.asyncio
async def test_retrieve_no_results_returns_empty(mock_embedder):
    vs = _make_vs_with_chunks([])
    retriever = Retriever(embedder=mock_embedder, vector_store=vs)
    results = await retriever.retrieve("obscure question")
    assert results == []

"""A11 — top_relevance must not read an UNSCORED injected profile card as the miss-signal.

The retriever entity-diversifies and injects a person's profile card at rank-0 with NO ce_score.
Reading chunks[0] then yields ~0.0 → a false primary_miss → spurious deep-fallback/live (the
"which professors study the brain" → McGill-visitor bug). With skip_unscored=True the signal is
the first chunk that actually carries a cross-encoder score. Flag off = today (reads chunks[0]).
"""
from types import SimpleNamespace

from v2.integration.retriever_shim import V2RetrieverShim


def _chunk(ce=None):
    # V1Chunk-shaped: metadata carries ce_score (None on an injected/unscored card)
    return SimpleNamespace(text="t", metadata={"ce_score": ce}, ce_score=None)


def _shim(reranker=None):
    s = V2RetrieverShim.__new__(V2RetrieverShim)   # no DB/embedder needed for top_relevance
    s.reranker = reranker
    return s


def test_skip_unscored_reads_first_scored_chunk():
    # rank-0 injected card (ce None) + real rank-1 chunk (ce 0.858) → 0.858, not ~0.0
    s = _shim()
    chunks = [_chunk(ce=None), _chunk(ce=0.858), _chunk(ce=0.4)]
    assert s.top_relevance("q", chunks, skip_unscored=True) == 0.858


def test_flag_off_reads_chunk0_today():
    # default (flag off) preserves today's behavior: reads chunks[0]; unscored → reranker pass
    fake_reranker = SimpleNamespace(score=lambda q, texts: [0.12])
    s = _shim(reranker=fake_reranker)
    chunks = [_chunk(ce=None), _chunk(ce=0.858)]
    assert s.top_relevance("q", chunks) == 0.12          # chunk0 rescored, NOT 0.858


def test_skip_unscored_all_unscored_falls_back_to_reranker():
    # no chunk carries a ce_score → today's tail path (rerank the first chunk)
    fake_reranker = SimpleNamespace(score=lambda q, texts: [0.33])
    s = _shim(reranker=fake_reranker)
    chunks = [_chunk(ce=None), _chunk(ce=None)]
    assert s.top_relevance("q", chunks, skip_unscored=True) == 0.33


def test_skip_unscored_first_chunk_scored_is_unchanged():
    # when rank-0 IS scored, skip_unscored returns it (no behavior change for the common case)
    s = _shim()
    chunks = [_chunk(ce=0.9), _chunk(ce=0.2)]
    assert s.top_relevance("q", chunks, skip_unscored=True) == 0.9


def test_empty_returns_none():
    s = _shim()
    assert s.top_relevance("q", [], skip_unscored=True) is None

from v2.core.retrieval.reranker import CrossEncoderReranker


def test_score_returns_none_when_model_absent(tmp_path, monkeypatch):
    # Empty model dir + block any download -> score() must return None, never raise.
    r = CrossEncoderReranker(model_dir=tmp_path / "nope")

    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(r, "_download", _boom)
    assert r.score("q", ["a", "b"]) is None
    assert r.available is False


def test_score_empty_passages_is_empty_list(tmp_path):
    r = CrossEncoderReranker(model_dir=tmp_path / "nope")
    assert r.score("q", []) == []


import pytest


@pytest.mark.slow
def test_orders_relevant_passage_first():
    """Downloads the real model once (network on first run). The relevant passage must
    outscore the off-topic one — guards real ms-marco output shape, not a mock."""
    r = CrossEncoderReranker()
    q = "Who chairs the GSA General Assembly meetings?"
    relevant = "Chair the General Assembly meetings and coordinate with Department Representatives."
    off = "Bi-weekly General Assembly Meetings begin no later than the third full week of classes."
    scores = r.score(q, [off, relevant])
    assert scores is not None
    assert scores[1] > scores[0]

"""Tests for the fuzzy search service."""

import pytest

from bot.services.search import SearchService


class TestSearchHits:
    """Queries that should return high-confidence results."""

    def test_exact_question_returns_result(self, search_svc: SearchService) -> None:
        results = search_svc.search("What is the GSA?")
        assert len(results) >= 1
        assert results[0].score >= 80

    def test_paraphrased_question_hits(self, search_svc: SearchService) -> None:
        # Query shares key tokens with "What is the GSA?" — token_set_ratio handles reordering
        results = search_svc.search("what is the GSA at NJIT")
        assert len(results) >= 1
        assert results[0].score >= 60

    def test_partial_keyword_hits(self, search_svc: SearchService) -> None:
        results = search_svc.search("funding opportunities")
        assert len(results) >= 1
        assert results[0].score >= 60

    def test_join_question_hits(self, search_svc: SearchService) -> None:
        results = search_svc.search("how to join GSA")
        assert len(results) >= 1
        assert results[0].score >= 60

    def test_mental_health_hits(self, search_svc: SearchService) -> None:
        results = search_svc.search("mental health counseling")
        assert len(results) >= 1
        assert results[0].score >= 60

    def test_results_ordered_by_score(self, search_svc: SearchService) -> None:
        results = search_svc.search("graduate student membership")
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_result_has_answer_text(self, search_svc: SearchService) -> None:
        results = search_svc.search("What is GSA")
        assert results[0].content != ""

    def test_result_respects_limit(self, search_svc: SearchService) -> None:
        results = search_svc.search("graduate", limit=2)
        assert len(results) <= 2


class TestSearchMisses:
    """Queries that should fail to meet the confidence threshold."""

    def test_gibberish_returns_empty(self, search_svc: SearchService) -> None:
        results = search_svc.search("xkqzzmvvppllargh")
        assert results == []

    def test_unrelated_query_returns_empty(self, search_svc: SearchService) -> None:
        results = search_svc.search("best pizza in new york city midtown")
        assert results == []

    def test_empty_query_returns_empty(self, search_svc: SearchService) -> None:
        results = search_svc.search("")
        assert results == []


class TestLowConfidenceFallback:
    """Verify the 60% confidence threshold is respected."""

    def test_below_threshold_excluded(self, search_svc: SearchService) -> None:
        """Results below MIN_CONFIDENCE should not appear."""
        results = search_svc.search("xkqzzmvv")
        for r in results:
            assert r.score >= search_svc.min_confidence

    def test_custom_threshold_respected(self, kb) -> None:
        high_threshold_svc = SearchService(kb, min_confidence=95.0)
        results = high_threshold_svc.search("GSA membership join")
        for r in results:
            assert r.score >= 95.0


class TestEmptyKnowledgeBase:
    """Edge cases when the knowledge base has no entries."""

    def test_empty_kb_returns_empty(self, tmp_path) -> None:
        from bot.services.knowledge_base import KnowledgeBase

        empty_kb = KnowledgeBase(data_dir=tmp_path)
        svc = SearchService(empty_kb)
        results = svc.search("What is GSA?")
        assert results == []

"""BM25 lexical index — exact and near-exact term retrieval over KB chunks.

Used alongside the vector store for hybrid retrieval. BM25 handles cases
where the embedding model has no semantic representation for a term (acronyms,
proper names, technical strings like "InstructEx", "MARCuS", "RACE-ESQ").
"""

import logging
import re

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


class BM25Index:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        tokenized = [_tokenize(c["text"]) for c in chunks]
        self.bm25 = BM25Okapi(tokenized)
        logger.info("BM25 index built: %d chunks", len(chunks))

    def search(self, query: str, n_results: int = 20) -> list[dict]:
        if not self.chunks:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        top_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:n_results]
        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                break
            entry = dict(self.chunks[idx])
            entry["bm25_score"] = float(scores[idx])
            results.append(entry)
        return results

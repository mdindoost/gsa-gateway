"""Pre-build diagnostic (senior review S2): for each GOLD question, find the fused rank of
the chunk that contains the gold fact, WITHOUT reranking. Tells us whether each miss is a
ranking failure (gold in pool, rank>1 -> reranker fixes it) or a recall failure (gold
outside pool_size -> we must widen recall, not just rerank).

FINDING (2026-06-16, pool_size=60): ranking-fixable=7, recall-miss=0. Every gold chunk is
already in the fused pool (worst is "who chairs GA" at rank 16; AirBNB rank 3). Reranking the
full pool suffices -- NO pool_size widening needed. The 4 "rank-1 already" cases were eval
misses from generation/diversification at limit=5, not retrieval rank.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.tests.rerank_gold import GOLD


def fused_rank(retr, query, substr):
    chunks = retr.retrieve(query, limit=50, group_by_entity=False)
    for i, ch in enumerate(chunks, start=1):
        if substr.lower() in (ch.content or "").lower():
            return i, len(chunks)
    return None, len(chunks)


def main():
    conn = get_connection("gsa_gateway.db")
    retr = V2Retriever(conn, Embedder())
    print(f"pool_size={retr.pool_size}")
    ranking, recall = 0, 0
    for q, sub in GOLD.items():
        rank, n = fused_rank(retr, q, sub)
        if rank is None:
            recall += 1
            verdict = "RECALL-MISS (widen pool)"
        elif rank == 1:
            verdict = "rank-1 already"
        else:
            ranking += 1
            verdict = f"RANKING-MISS rank={rank}"
        print(f"  {verdict:<28} pool={n:<3} | {q[:55]}")
    print(f"\nsummary: ranking-fixable={ranking}  recall-miss={recall}")


if __name__ == "__main__":
    main()

from __future__ import annotations
from eval.processing_debt.types import XRay

def _route(conn, q):
    from v2.core.retrieval.router import route
    return route(conn, q)

def _fused_pool(conn, q, emb):
    """RAW, generous candidate pool BEFORE rerank/entity-grouping. group_by_entity=False so entity dedup
    can't hide a chunk; limit = max(100, 2*pool_size) so truncation can't cause a false POOL verdict."""
    from v2.core.retrieval.retriever import V2Retriever
    ret = V2Retriever(conn, emb)
    limit = max(100, 2 * getattr(ret, "pool_size", 40))
    return ret.retrieve(q, limit=limit, group_by_entity=False)

def _reranked(conn, q, emb, rer):
    """Production-config reranked top-5 (group_by_entity defaults True — matches the answer path)."""
    from v2.core.retrieval.retriever import V2Retriever
    return V2Retriever(conn, emb, reranker=rer).retrieve(q, limit=5)

def xray(conn, question, *, embedder=None, reranker=None) -> XRay:
    if embedder is None:
        from v2.core.retrieval.embedder import Embedder
        embedder = Embedder()
    if reranker is None:
        from v2.core.retrieval.reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker()
    r = _route(conn, question)
    fused = _fused_pool(conn, question, embedder)
    reranked = _reranked(conn, question, embedder, reranker)
    ce = {c.item_id: c.ce_score for c in reranked if getattr(c, "ce_score", None) is not None}
    return XRay(question=question,
                router_family=None,                          # dropped: Route has no .family
                router_skill=getattr(r, "skill", None),
                fused_pool_ids=[c.item_id for c in fused],
                top5_ids=[c.item_id for c in reranked[:5]],
                ce_scores=ce,
                tier_primary_miss=(len(reranked) == 0),
                answer=None)

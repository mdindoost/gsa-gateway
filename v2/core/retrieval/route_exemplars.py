"""Kavosh v2.1 router exemplars + masker loader (Phase 1b).

This module provides the router-space embed (FIXED `router_query:` prefix, distinct from the
retriever's `search_query:` space), the encoder version stamp/guard, and (Task B2) the exemplar
+ DB-masker loader that builds the production masked-coarse classifier.
"""
from __future__ import annotations

ROUTER_PREFIX = "router_query: "


def router_embed(embedder, text: str):
    """Embed the RAW message under the FIXED router prefix (NOT search_query:). The router classifier
    and the retriever live in two prefix spaces (the classify encode is router-prefixed; a RAG
    fall-through re-encodes in the retriever's search_query space — see the one-encode framing in
    Global Constraints)."""
    return _embed_with_prefix(embedder, ROUTER_PREFIX + text)


def _embed_with_prefix(embedder, prefixed_text: str):
    # Embedder.embed_query hardcodes "search_query: "; for the router space we go through the
    # ._embed + .normalize pair directly so ONLY ROUTER_PREFIX is applied. The real Embedder
    # (v2/core/retrieval/embedder.py) has _embed(text)->list|None (L27) and a @staticmethod
    # normalize (L37), so this is the single embed path for the router space.
    vec = embedder._embed(prefixed_text)         # noqa: SLF001 - intentional: single embed path
    return embedder.normalize(vec)


def encoder_stamp(embedder) -> dict:
    return {"model": embedder.model, "base_url": embedder.base_url, "prefix": ROUTER_PREFIX}


def verify_stamp(embedder, stamp: dict) -> None:
    current = encoder_stamp(embedder)
    if current != stamp:
        raise RuntimeError(
            f"Router encoder drift — exemplars were built with {stamp} but runtime is {current}. "
            "Rebuild exemplars or fix EMBEDDING_MODEL/OLLAMA_URL.")

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


import json
from pathlib import Path
import numpy as np
from v2.core.retrieval.route_classifier import RouteClassifier

DEFAULT_EXEMPLARS = "eval/router/labeled_routes.jsonl"


def load_exemplars(path: str = DEFAULT_EXEMPLARS):
    """Exemplars = seeds + TRAIN rows. EXCLUDES split in ("test","hardneg"): the 97 split:test rows
    are the held-out GOLD (never train on them — see review B-1) and hardneg is the boundary suite.
    Mirrors v2/eval/router/bakeoff.py::_partition's exemplar pool exactly."""
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d.get("split") in ("test", "hardneg"):
            continue
        if d.get("query") and d.get("family"):
            rows.append((d["query"], d["family"]))
    return rows


def build_masker(conn):
    """Reuse the validated DB-built slot masker (org names/slugs/aliases + person names/surnames)."""
    from v2.eval.router.mask import build_masker_from_db
    return build_masker_from_db(conn)


def _encode_prefixed(embedder, texts):
    """Encode a list of RAW texts in the router-prefix space, L2-normalized. Uses the embedder's
    batch path (one HTTP call) when available — keeps the ~500-exemplar startup fit fast (review
    S-2) — else falls back to serial single embeds."""
    prefixed = [ROUTER_PREFIX + t for t in texts]
    batch = getattr(embedder, "_embed_batch", None)
    if callable(batch):
        vecs = batch(prefixed)
        return np.array([embedder.normalize(v) for v in vecs])
    return np.array([_embed_with_prefix(embedder, t) for t in texts])


def build_classifier(conn, embedder, path: str = DEFAULT_EXEMPLARS) -> RouteClassifier:
    exemplars = load_exemplars(path)
    masker = build_masker(conn)
    encode_fn = lambda texts: _encode_prefixed(embedder, texts)
    return RouteClassifier(exemplars, encode_fn, masker)

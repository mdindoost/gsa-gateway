"""Embedding client for v2 retrieval.

Ollama ``nomic-embed-text`` with the same conventions Step 3 used to populate
``knowledge_vectors``: documents prefixed ``search_document: ``, queries prefixed
``search_query: ``, and every vector L2-normalized so the vec0 ``FLOAT[768]``
(default L2 distance) ranks identically to cosine.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
EMBED_DIM = 768


class Embedder:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.embed_url = f"{self.base_url}/api/embed"

    def _embed(self, text: str, timeout: int = 30) -> list[float] | None:
        payload = json.dumps({"model": self.model, "input": text}).encode()
        req = urllib.request.Request(
            self.embed_url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        emb = data.get("embeddings")
        return emb[0] if emb and emb[0] else None

    def _embed_batch(self, texts: list[str], timeout: int = 60) -> list[list[float] | None]:
        """Embed many texts in ONE /api/embed call (Ollama accepts a list `input`). Used by the
        router classifier fit (~500 exemplars at startup) to avoid serial HTTP round-trips."""
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            self.embed_url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        embs = data.get("embeddings") or []
        # Pad/truncate defensively so the result aligns 1:1 with `texts`.
        out: list[list[float] | None] = []
        for i in range(len(texts)):
            out.append(embs[i] if i < len(embs) and embs[i] else None)
        return out

    @staticmethod
    def normalize(vec: list[float] | None) -> list[float] | None:
        if vec is None:
            return None
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else None

    def embed_query(self, text: str) -> list[float] | None:
        return self.normalize(self._embed(f"search_query: {text.strip()[:2000]}"))

    def embed_document(self, text: str) -> list[float] | None:
        return self.normalize(self._embed(f"search_document: {text.strip()[:2000]}"))

    def health_check(self) -> bool:
        try:
            v = self._embed("search_document: health check", timeout=15)
        except Exception:  # noqa: BLE001
            return False
        return v is not None and len(v) == EMBED_DIM


def embed_with_retry(call, attempts: int = 3, backoff: float = 0.5):
    """Retry/backoff policy wrapper around a RAW embed callable.

    `call` is a zero-arg callable returning ``list[float] | None``. Returns the first
    non-None result; on None or exception, sleeps ``backoff * attempt`` and retries up to
    ``attempts`` total. Returns None after the last attempt. Never raises. Does NOT
    normalize — the caller normalizes once at its write site.
    """
    for attempt in range(1, attempts + 1):
        try:
            vec = call()
            if vec is not None:
                return vec
        except Exception:  # noqa: BLE001 - transient timeout/conn reset; retry then give up
            pass
        if attempt < attempts and backoff:
            time.sleep(backoff * attempt)
    return None

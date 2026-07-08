"""Embedding client for v2 retrieval.

Descriptor-driven (the LLM-agnostic seam): model name, vector dimension, the
asymmetric embed prefixes, and truncation ALL read from the active
``ModelDescriptor`` — never a hardcoded constant. For the production model
(Qwen3-Embedding-0.6B) that means the QUERY is wrapped in the ``Instruct: …
\\nQuery: `` template and PASSAGES are embedded RAW; for nomic-embed-text it
means the ``search_document: ``/``search_query: `` prefixes. Every vector is
L2-normalized so the vec0 ``FLOAT[dim]`` (default L2 distance) ranks identically
to cosine.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request

from v2.core.retrieval.model_descriptor import active_descriptor

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


class Embedder:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.descriptor = active_descriptor()
        self.model = model or self.descriptor.ollama_name
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

    def _prepare(self, prefix: str, text: str) -> str:
        d = self.descriptor
        return prefix + d.truncate_to_tokens(text.strip(), d.context_window)

    def embed_query(self, text: str) -> list[float] | None:
        return self.normalize(self._embed(self._prepare(self.descriptor.query_prefix, text)))

    def embed_document(self, text: str) -> list[float] | None:
        return self.normalize(self._embed(self._prepare(self.descriptor.doc_prefix, text)))

    def embed_documents(self, texts: list[str]) -> list[list[float] | None]:
        """Batch-embed passages (doc prefix + truncate + L2-normalize). For the area-tag vocabulary."""
        prepared = [self._prepare(self.descriptor.doc_prefix, t) for t in texts]
        return [self.normalize(v) for v in self._embed_batch(prepared)]

    def health_check(self) -> bool:
        try:
            v = self._embed(self._prepare(self.descriptor.doc_prefix, "health check"), timeout=15)
        except Exception:  # noqa: BLE001
            return False
        return v is not None and len(v) == self.descriptor.dim


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

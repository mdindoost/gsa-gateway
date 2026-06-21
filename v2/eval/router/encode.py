from __future__ import annotations
import hashlib
import numpy as np

ROUTER_PREFIX = "router_query: "


class FakeEncoder:
    """Deterministic bag-of-token-hash encoder — no network. For unit tests only."""
    def __init__(self, dim: int = 8):
        self.dim = dim

    def __call__(self, texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            v = np.zeros(self.dim)
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            n = np.linalg.norm(v)
            rows.append(v / n if n else v)
        return np.array(rows)


def real_encoder(texts: list[str]) -> np.ndarray:
    """Wraps the production nomic embedder with the fixed router prefix. CLI-only (needs Ollama).

    ``Embedder.embed_query`` already prepends ``search_query: `` and L2-normalizes; the fixed
    ``ROUTER_PREFIX`` is applied uniformly to every text so exemplars and queries share one space.
    """
    from v2.core.retrieval.embedder import Embedder
    enc = Embedder()
    rows = []
    for t in texts:
        v = enc.embed_query(ROUTER_PREFIX + t)
        if v is None:
            raise RuntimeError(f"embed_query returned None for {t!r} (is Ollama up?)")
        rows.append(v)
    return np.array(rows)

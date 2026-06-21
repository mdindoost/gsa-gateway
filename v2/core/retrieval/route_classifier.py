"""Production masked exemplar-NN family classifier (Kavosh v2.1 Phase 1b).

The validated Phase-1a `masked_coarse` mechanism, productionized (no dependency on v2/eval).
It picks the coarse FAMILY only (KG / RAG / COMMAND / CLARIFY / OTHER / LIVE); the SQL skill
stays deterministic in the resolver.
"""
from __future__ import annotations
import numpy as np


class RouteClassifier:
    """Masked exemplar-NN family classifier (the Phase-1a `masked_coarse` mechanism, productionized).
    Picks the coarse FAMILY only; the SQL skill stays deterministic in the resolver."""

    def __init__(self, exemplars, encode_fn, masker):
        self.encode_fn = encode_fn
        self.masker = masker
        self.row_label = [fam for _q, fam in exemplars]
        masked = [masker.mask(q) for q, _fam in exemplars]
        self.mat = encode_fn(masked) if masked else np.zeros((0, 0))

    def ranked(self, query: str):
        if self.mat.shape[0] == 0:
            return []
        q = self.encode_fn([self.masker.mask(query)])[0]
        sims = self.mat @ q
        best: dict[str, float] = {}
        for lab, s in zip(self.row_label, sims):
            if s > best.get(lab, -1.0):
                best[lab] = float(s)
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)

    def top(self, query: str):
        r = self.ranked(query)
        if not r:
            return ("", 0.0, 0.0)
        label, score = r[0]
        margin = score - (r[1][1] if len(r) > 1 else 0.0)
        return (label, score, float(margin))

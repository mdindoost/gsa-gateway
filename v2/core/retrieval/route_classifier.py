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
        masked = [masker.mask(q) for q, _fam in exemplars]
        raw = encode_fn(masked) if masked else []
        # Drop any exemplar whose embed failed (None) — with its label — so `mat` is a CLEAN float
        # matrix. A single None left in here would make np.array ragged/object-dtype and every
        # decide() matmul would raise (then get silently swallowed → classifier dead). [review F6]
        rows: list = []
        labels: list = []
        for vec, (_q, fam) in zip(raw, exemplars):
            if vec is None:
                continue
            rows.append(np.asarray(vec, dtype=float))
            labels.append(fam)
        self.row_label = labels
        self.mat = np.array(rows, dtype=float) if rows else np.zeros((0, 0))

    def ranked(self, query: str):
        if self.mat.shape[0] == 0:
            return []
        enc = self.encode_fn([self.masker.mask(query)])
        q = enc[0] if len(enc) else None
        if q is None:                       # query embed failed → cannot classify; degrade gracefully
            return []
        q = np.asarray(q, dtype=float)
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

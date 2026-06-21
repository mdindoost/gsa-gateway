from __future__ import annotations
import numpy as np
from v2.eval.router.types import LabeledExample


def _label_of(ex: LabeledExample, level: str) -> str:
    if level == "family":
        return ex.family
    if ex.family == "KG":
        return f"KG/{ex.skill}"
    if ex.family == "RAG":
        return f"RAG/{ex.source}"
    return ex.family


class ExemplarClassifier:
    def __init__(self, level: str = "family"):
        assert level in ("family", "skill")
        self.level = level
        self.labels: list[str] = []
        self.mat: np.ndarray | None = None      # rows = exemplar vectors
        self.row_label: list[str] = []

    def fit(self, exemplars: list[LabeledExample], encoder) -> "ExemplarClassifier":
        self.row_label = [_label_of(e, self.level) for e in exemplars]
        self.labels = sorted(set(self.row_label))
        self.mat = encoder([e.query for e in exemplars])
        return self

    def predict(self, query: str, encoder) -> list[tuple[str, float]]:
        q = encoder([query])[0]
        sims = self.mat @ q                      # cosine (rows are L2-normalized)
        best: dict[str, float] = {}
        for lab, s in zip(self.row_label, sims):
            if s > best.get(lab, -1.0):
                best[lab] = float(s)
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)

    def top(self, query: str, encoder) -> tuple[str, float, float]:
        ranked = self.predict(query, encoder)
        if not ranked:
            return ("", 0.0, 0.0)
        label, score = ranked[0]
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        return (label, score, float(margin))

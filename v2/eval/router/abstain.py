"""Confidence-gated abstention: route low-confidence predictions to CLARIFY instead of a confident
wrong skill. The classifier already returns (score, margin); the bake-off never used them. Gating on
margin directly attacks wrong_confident_exact / false_honest_partial — the anti-fab failures.
"""
from __future__ import annotations
from v2.eval.router.types import RoutePrediction, Family


class AbstainingArm:
    """Wrap an arm that yields score/margin; below threshold -> CLARIFY. Pass through if no score."""
    def __init__(self, inner, score_min: float = 0.0, margin_min: float = 0.0):
        self.inner = inner
        self.score_min = score_min
        self.margin_min = margin_min

    def predict(self, query: str) -> RoutePrediction:
        p = self.inner.predict(query)
        if p.score is None:                          # inner gave no confidence -> trust it
            return p
        if p.score < self.score_min or (p.margin is not None and p.margin < self.margin_min):
            return RoutePrediction(family=Family.CLARIFY, score=p.score, margin=p.margin)
        return p


def _is_correct(ex, label: str, level: str) -> bool:
    if level == "family":
        return label == ex.family
    if ex.family == "KG":
        return label == f"KG/{ex.skill}"
    if ex.family == "RAG":
        return label == f"RAG/{ex.source}"
    return label == ex.family


def calibrate_thresholds(clf, examples, encoder, level: str = "family",
                         target_precision: float = 0.9) -> tuple[float, float, bool]:
    """Pick a margin threshold from the given examples (use TRAIN/VAL, never test). Prefer the
    threshold meeting target precision at MAX coverage; if none meets it, fall back to the threshold
    with the HIGHEST precision (tie -> max coverage) so abstention still does something — and report
    met=False so the degeneration is LOUD, not a silent no-op. Returns (score_min, margin_min, met).
    """
    rows = []
    for ex in examples:
        label, _score, margin = clf.top(ex.query, encoder)
        rows.append((margin, _is_correct(ex, label, level)))
    if not rows:
        return (0.0, 0.0, False)
    meeting_margin, meeting_cov = None, -1.0
    fallback_margin, fallback_key = 0.0, (-1.0, -1.0)     # (precision, coverage)
    for t in [0.0] + sorted({m for m, _ in rows}):
        kept = [(m, c) for m, c in rows if m >= t]
        if not kept:
            continue
        prec = sum(1 for _, c in kept if c) / len(kept)
        cov = len(kept) / len(rows)
        if prec >= target_precision and cov > meeting_cov:
            meeting_margin, meeting_cov = t, cov
        if (prec, cov) > fallback_key:
            fallback_key, fallback_margin = (prec, cov), t
    if meeting_margin is not None:
        return (0.0, meeting_margin, True)
    return (0.0, fallback_margin, False)

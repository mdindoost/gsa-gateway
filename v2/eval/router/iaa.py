"""Inter-annotator agreement + review-honesty metrics for the labeling protocol.

- cohen_kappa: agreement on a double-labeled sample (gate: κ ≥ 0.8 on family before scaling).
- edit_rate: fraction of LLM-proposed labels the human CHANGED — a near-zero rate on harvested
  noise is a rubber-stamp red flag (the human isn't really reviewing).
"""
from __future__ import annotations


def cohen_kappa(a: list[str], b: list[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("cohen_kappa needs two equal, non-empty label lists")
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1.0 - pe)


def edit_rate(rows) -> float:
    """Fraction of rows where the human's family differs from the LLM's proposed_family.
    Rows without a proposed_family are ignored (no proposal to audit)."""
    scored = [r for r in rows if r.proposed_family is not None]
    if not scored:
        return 0.0
    return sum(1 for r in scored if r.family != r.proposed_family) / len(scored)

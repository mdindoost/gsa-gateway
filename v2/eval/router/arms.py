from __future__ import annotations
from v2.eval.router.types import RoutePrediction, Family


def from_route(route_obj) -> RoutePrediction:
    """Adapt the production router's Route (or None) to a RoutePrediction.
    None means the deterministic router abstained → falls to RAG/general in production."""
    if route_obj is None:
        return RoutePrediction(family=Family.RAG, source="general")
    if route_obj.skill == "person_disambig":
        return RoutePrediction(family=Family.KG, skill="person_disambig", slots=dict(route_obj.args))
    return RoutePrediction(family=Family.KG, skill=route_obj.skill, slots=dict(route_obj.args))


def _parse_label(label: str) -> tuple[str, str | None, str | None]:
    if "/" not in label:
        return (label, None, None)
    fam, tail = label.split("/", 1)
    return (fam, tail if fam == "KG" else None, tail if fam == "RAG" else None)


class DetectorFirstArm:
    """Arm 1: today's behavior — the real deterministic router; None → RAG/general."""
    def __init__(self, conn):
        self.conn = conn

    def predict(self, query: str) -> RoutePrediction:
        from v2.core.retrieval import router as srouter
        return from_route(srouter.route(self.conn, query))


class CoarseThenDeterministicArm:
    """Arm 2 (the spec default): classify coarse family; if KG, defer to the deterministic router."""
    def __init__(self, conn, classifier, encoder):
        self.conn, self.clf, self.enc = conn, classifier, encoder

    def predict(self, query: str) -> RoutePrediction:
        from v2.core.retrieval import router as srouter
        fam, score, margin = self.clf.top(query, self.enc)   # family-level classifier
        if fam == Family.KG:
            p = from_route(srouter.route(self.conn, query))
            p.score, p.margin = score, margin
            return p
        return RoutePrediction(family=fam, source=("general" if fam == Family.RAG else None),
                               score=score, margin=margin)


class FullClassifierArm:
    """Arm 3: the classifier picks family AND skill/source directly."""
    def __init__(self, classifier, encoder):
        self.clf, self.enc = classifier, encoder

    def predict(self, query: str) -> RoutePrediction:
        label, score, margin = self.clf.top(query, self.enc)
        fam, skill, src = _parse_label(label)
        return RoutePrediction(family=fam, skill=skill, source=src, score=score, margin=margin)

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


def kg_recall_bias(ranked, route_fn, conn, query, margin_max):
    """Inverse-FN offline analogue (spec §5 step 4): if the classifier put RAG on top but KG is a
    close runner-up AND the deterministic router actually resolves a skill, prefer the resolved KG
    route. Returns the resolved RoutePrediction or None (no change). Exactness is preserved because
    the route comes from the deterministic router, never from the classifier."""
    if not ranked or ranked[0][0] != Family.RAG:
        return None
    kg = next((s for lab, s in ranked if lab == Family.KG), None)
    if kg is None or (ranked[0][1] - kg) > margin_max:
        return None
    r = route_fn(conn, query)
    if r is None:
        return None
    return from_route(r)


class KGRecallBiasedArm:
    """coarse_then_deterministic + the inverse-FN bias (Lever #1 experiment B)."""
    def __init__(self, conn, classifier, encoder, margin_max=0.05):
        self.conn, self.clf, self.enc, self.margin_max = conn, classifier, encoder, margin_max

    def predict(self, query: str) -> RoutePrediction:
        from v2.core.retrieval import router as srouter
        ranked = self.clf.predict(query, self.enc)
        biased = kg_recall_bias(ranked, srouter.route, self.conn, query, self.margin_max)
        if biased is not None:
            biased.score = ranked[0][1]
            return biased
        fam, score, margin = self.clf.top(query, self.enc)
        if fam == Family.KG:
            p = from_route(srouter.route(self.conn, query))
            p.score, p.margin = score, margin
            return p
        return RoutePrediction(family=fam, source=("general" if fam == Family.RAG else None),
                               score=score, margin=margin)


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

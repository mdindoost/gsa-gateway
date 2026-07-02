"""Routing-slot scoring for the slot-extraction gate (Workstream 1, deferred slot-F1 gate).

Measures how well the extractor FILLS slots (not just picks the skill). Compares predicted natural
slots to gold natural slots as (key, canonical-value) pairs, micro-averaged P/R/F1 + exact-match +
per-skill. Only ROUTING slot keys are scored (annotation keys like note/expected are ignored — the
RAG review flagged that gold dicts mix the two). org/person are compared by RESOLVED id (so "mie" and
"mechanical engineering" — same org, different surface — count as equal); enums/scalars by normalized
value; area by normalized string.
"""
from __future__ import annotations
from collections import Counter, defaultdict

from v2.core.retrieval import entity
from v2.core.retrieval import router as srouter

# The slot keys that are part of routing (everything else in a gold dict is annotation).
ROUTING_SLOT_KEYS = ("person", "org", "area", "metric", "profile", "role", "order", "n")


def _canon(conn, key: str, value) -> str | None:
    """Canonical comparable for a slot value, or None if effectively empty. org/person resolve to a
    real id so differently-worded-but-same entities match; other keys normalize their surface."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if key == "org":
        oid, _ = srouter._find_org(conn, s)
        return f"org:{oid}" if oid is not None else f"orgstr:{s}"
    if key == "person":
        hits = entity.resolve_people(conn, s) or (
            entity.persons_by_lastname(conn, s) if len(s.split()) == 1 else [])
        if len(hits) == 1:
            return f"pid:{hits[0]['entity_id']}"
        return f"personstr:{s}"
    if key == "role":
        return srouter._ROLE_SYNONYM.get(s, s)
    if key == "n":
        try:
            return f"n:{int(float(s))}"
        except ValueError:
            return f"n:{s}"
    return s                                   # area / metric / profile / order


def _pairset(conn, slots: dict) -> set:
    out = set()
    for k in ROUTING_SLOT_KEYS:
        c = _canon(conn, k, (slots or {}).get(k))
        if c is not None:
            out.add((k, c))
    return out


def slot_score(items, conn) -> dict:
    """items: iterable of (gold_skill, gold_slots, pred_slots). Micro P/R/F1 over routing-slot pairs +
    exact-match rate + per-skill F1. Rows where the prediction filled no slots contribute their gold
    slots as false negatives (recall penalty), which is the correct 'routed but didn't slot it' signal."""
    tp = fp = fn = 0
    exact = 0
    n = 0
    per = defaultdict(lambda: Counter())
    for gold_skill, gold_slots, pred_slots in items:
        n += 1
        g = _pairset(conn, gold_slots)
        p = _pairset(conn, pred_slots)
        r_tp, r_fp, r_fn = len(g & p), len(p - g), len(g - p)
        tp += r_tp; fp += r_fp; fn += r_fn
        if g == p:
            exact += 1
        per[gold_skill]["tp"] += r_tp; per[gold_skill]["fp"] += r_fp; per[gold_skill]["fn"] += r_fn

    def prf(t, f, n_):
        pr = t / (t + f) if (t + f) else (1.0 if t == 0 and f == 0 and n_ == 0 else 0.0)
        rc = t / (t + n_) if (t + n_) else 1.0
        f1 = 2 * pr * rc / (pr + rc) if (pr + rc) else 0.0
        return round(pr, 4), round(rc, 4), round(f1, 4)

    pr, rc, f1 = prf(tp, fp, fn)
    per_skill = {sk: dict(zip(("precision", "recall", "f1"), prf(c["tp"], c["fp"], c["fn"])))
                 for sk, c in sorted(per.items())}
    return {"slot_precision": pr, "slot_recall": rc, "slot_f1": f1,
            "slot_exact_match": round(exact / n, 4) if n else None, "n_rows": n,
            "per_skill": per_skill}

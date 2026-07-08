"""Gold precision gate for the entity-mentions resolution gate (spec §5.4 / R10).

Pins BOTH sides: curated bio + genuine news must be ACCEPTED; memorial-substring +
roster page must be REJECTED. Precision must stay >= 0.9 on the labeled set.
"""
from v2.core.ingestion.entity_mentions import resolve_item, PersonName

ORIA = PersonName(1, "k/oria", "Oria", "Vincent")
DEEK = PersonName(2, "k/deek", "Deek", "Fadi")


def _others(n):
    return [PersonName(10 + i, f"k/o{i}", f"L{i}", f"F{i}") for i in range(n)]


# (title, content, people, target_key, should_accept)
CASES = [
    ("Who is Prof. Vincent Oria?", "Vincent Oria is a Professor and Chair.", [ORIA], "k/oria", True),
    ("MMI 2026", "Committee: Vincent Oria (NJIT).", [ORIA], "k/oria", True),
    ("News", "From Byblos to Newark: Fadi Deek’s memoir. Fadi Deek reflects with F0 L0 and F1 L1.",
     [DEEK] + _others(2), "k/deek", True),                                 # genuine multi-person accept
    ("Award", "2010 Franklin V. Taylor Memorial Award", [ORIA], "k/oria", False),  # 'oria' in memorial
    ("Ph.D. Computer Science",
     "Professor Oria, Vincent " + " ".join(f"F{i} L{i}" for i in range(6)),
     [ORIA] + _others(6), "k/oria", False),                               # roster
]


def test_gold_precision():
    tp = fp = fn = 0
    for title, body, people, target, want in CASES:
        got = any(p.node_key == target for p, _, _ in resolve_item(title, body, people))
        if want and got:
            tp += 1
        elif got and not want:
            fp += 1
        elif want and not got:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    assert prec >= 0.9, f"precision {prec:.3f} (tp={tp} fp={fp})"
    assert rec >= 0.9, f"recall {rec:.3f} (tp={tp} fn={fn})"

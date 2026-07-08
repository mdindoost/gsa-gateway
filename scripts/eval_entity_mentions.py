"""Gold precision/recall gate for the entity-mentions resolution gate.

Runs the labeled accept/reject set and exits non-zero if precision < 0.9 — the
acceptance instrument to re-run whenever the gate or ROSTER_N changes.
Usage: python scripts/eval_entity_mentions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.ingestion.entity_mentions import resolve_item, PersonName

ORIA = PersonName(1, "k/oria", "Oria", "Vincent")
DEEK = PersonName(2, "k/deek", "Deek", "Fadi")


def _others(n):
    return [PersonName(10 + i, f"k/o{i}", f"L{i}", f"F{i}") for i in range(n)]


CASES = [
    ("Who is Prof. Vincent Oria?", "Vincent Oria is a Professor and Chair.", [ORIA], "k/oria", True),
    ("MMI 2026", "Committee: Vincent Oria (NJIT).", [ORIA], "k/oria", True),
    ("News", "From Byblos to Newark: Fadi Deek’s memoir. Fadi Deek reflects with F0 L0 and F1 L1.",
     [DEEK] + _others(2), "k/deek", True),
    ("Award", "2010 Franklin V. Taylor Memorial Award", [ORIA], "k/oria", False),
    ("Ph.D. Computer Science",
     "Professor Oria, Vincent " + " ".join(f"F{i} L{i}" for i in range(6)),
     [ORIA] + _others(6), "k/oria", False),
]


def main() -> int:
    tp = fp = fn = 0
    for title, body, people, target, want in CASES:
        got = any(p.node_key == target for p, _, _ in resolve_item(title, body, people))
        mark = "OK " if got == want else "XX "
        print(f"  {mark} want={int(want)} got={int(got)}  {title!r}")
        tp += want and got
        fp += got and not want
        fn += want and not got
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"precision={prec:.3f} recall={rec:.3f} (tp={tp} fp={fp} fn={fn})")
    if prec < 0.9:
        print("FAIL: precision below 0.9")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

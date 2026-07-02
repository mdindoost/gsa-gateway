"""Workstream-1 merge gate (design §7 Option A): BEFORE vs AFTER on the blind test split + hardneg.

BEFORE = coarse_then_deterministic (production today). AFTER = same + constrained-JSON slot-extraction
fallback (real Granite). Reuses the real qwen encoder + masker + exemplar classifier + the 97-row
explicit blind test + the 23 hardneg rows.

Gates (Option A): (a) family accuracy on blind test does NOT regress; (b) the 3 regression paraphrases
route to the right KG skill; (c) hardneg = 0 NEW KG mis-fires vs BEFORE. (Slot-F1 gate deferred until
the 39 KG test rows are blind-slot-labeled.)

Usage: python scripts/router_slot_bakeoff.py [labeled_routes.jsonl] [gsa_gateway.db]  (needs Ollama up)
"""
from __future__ import annotations
import sqlite3
import sys
import time
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.services.ollama_client import generate_json_sync
from v2.eval.router.arms import CoarseThenDeterministicArm, CoarseThenExtractorArm
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.dataset import load_dataset
from v2.eval.router.encode import real_encoder
from v2.eval.router.mask import MaskedEncoder, build_masker_from_db
from v2.eval.router.metrics import score

REGRESSIONS = [
    ("which prof does ML in computing", "people_by_research_area"),
    ("can you tell me a bit about professor Koutis?", "entity_card"),
    ("I'm trying to reach someone named Koutis", "entity_card"),
]


def _fam_acc(arm, rows):
    pairs = [(ex, arm.predict(ex.query)) for ex in rows]
    return score(pairs), pairs


def main():
    data = sys.argv[1] if len(sys.argv) > 1 else "eval/router/labeled_routes.jsonl"
    db = sys.argv[2] if len(sys.argv) > 2 else "gsa_gateway.db"
    examples = load_dataset(data)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    train = [x for x in examples if x.split not in ("test", "hardneg")]
    test = [x for x in examples if x.split == "test"]
    hardneg = [x for x in examples if x.split == "hardneg"]
    print(f"train={len(train)} test={len(test)} hardneg={len(hardneg)}")

    masker = build_masker_from_db(conn)
    menc = MaskedEncoder(real_encoder, masker)
    fam = ExemplarClassifier(level="family").fit(train, menc)

    gen = partial(generate_json_sync, model="granite4:tiny-h", timeout=8.0)
    before = CoarseThenDeterministicArm(conn, fam, menc)
    after = CoarseThenExtractorArm(conn, fam, menc, generate_json=gen, tau=0.0)

    # (a) family accuracy on blind test
    t0 = time.time()
    b_score, _ = _fam_acc(before, test)
    a_score, _ = _fam_acc(after, test)
    print("\n=== BLIND TEST (family accuracy) ===")
    print(f"  BEFORE family_acc = {b_score['family_accuracy']:.3f}  skill_acc = {b_score['skill_accuracy']}")
    print(f"  AFTER  family_acc = {a_score['family_accuracy']:.3f}  skill_acc = {a_score['skill_accuracy']}")
    gate_a = a_score["family_accuracy"] >= b_score["family_accuracy"] - 1e-9

    # (b) regression paraphrases (real extraction)
    print("\n=== REGRESSION PARAPHRASES ===")
    gate_b = True
    for q, want in REGRESSIONS:
        p = after.predict(q)
        ok = (p.family == "KG" and p.skill == want)
        gate_b = gate_b and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {q!r} -> {p.family}/{p.skill} (want KG/{want})")

    # (c) hardneg: 0 NEW KG mis-fires vs BEFORE
    print("\n=== HARDNEG (new KG mis-fires) ===")
    new_misfires = []
    for ex in hardneg:
        pb, pa = before.predict(ex.query), after.predict(ex.query)
        if pa.family == "KG" and pb.family != "KG":
            new_misfires.append((ex.query, pa.skill))
    for q, sk in new_misfires:
        print(f"  NEW-KG: {q!r} -> KG/{sk}")
    gate_c = len(new_misfires) == 0
    print(f"  new KG mis-fires: {len(new_misfires)}")

    dt = time.time() - t0
    print(f"\n=== GATES ===  latency(all rows) {dt:.1f}s over {len(test)+len(hardneg)} rows")
    print(f"  (a) family non-regression : {'PASS' if gate_a else 'FAIL'}")
    print(f"  (b) regression paraphrases: {'PASS' if gate_b else 'FAIL'}")
    print(f"  (c) hardneg no new misfire: {'PASS' if gate_c else 'FAIL'}")
    print("MERGE GATE:", "PASS" if (gate_a and gate_b and gate_c) else "FAIL")


if __name__ == "__main__":
    main()

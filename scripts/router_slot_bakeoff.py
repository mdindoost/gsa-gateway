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
from v2.eval.router.slot_metrics import ROUTING_SLOT_KEYS, slot_score
from v2.core.retrieval import router as srouter
from v2.core.retrieval.slot_extractor import extract_slots, resolve_and_validate

REGRESSIONS = [
    ("which prof does ML in computing", "people_by_research_area"),
    ("can you tell me a bit about professor Koutis?", "entity_card"),
    ("I'm trying to reach someone named Koutis", "contact_of_person"),
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
    b_score, b_pairs = _fam_acc(before, test)
    a_score, a_pairs = _fam_acc(after, test)
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

    # (d) DEFERRED slot-F1 gate — active once the 39 KG test rows carry gold routing slots.
    print("\n=== SLOT-F1 (extractor-path KG test rows) ===")
    def _routing(slots):
        return {k: v for k, v in (slots or {}).items() if k in ROUTING_SLOT_KEYS and str(v).strip()}
    gold_rows = [ex for ex in test if ex.family == "KG" and _routing(ex.slots)]
    if not gold_rows:
        print("  SKIPPED — no gold routing slots yet. Fill eval/router/slot_gold_worksheet.jsonl and")
        print("  merge into labeled_routes.jsonl to activate this gate.")
        gate_d = None
    else:
        before_items, after_items = [], []
        for ex in gold_rows:
            if srouter.route(conn, ex.query) is not None:
                continue                                   # regex-path (deterministic) — not the extractor's job
            ext = extract_slots(ex.query, gen)
            pred = {}
            if ext.skill != "none":
                r = resolve_and_validate(conn, ext.skill, ext.slots, ex.query)
                if r is not None and r.skill == ex.skill:
                    pred = ext.slots
            before_items.append((ex.skill, _routing(ex.slots), {}))       # BEFORE = RAG, no slots
            after_items.append((ex.skill, _routing(ex.slots), pred))
        if not after_items:
            print("  (no extractor-path rows among gold-slot rows)")
            gate_d = None
        else:
            b = slot_score(before_items, conn); a = slot_score(after_items, conn)
            print(f"  rows={a['n_rows']}  BEFORE slot_f1={b['slot_f1']}  AFTER slot_f1={a['slot_f1']} "
                  f"(P={a['slot_precision']} R={a['slot_recall']} exact={a['slot_exact_match']})")
            gate_d = a["slot_f1"] > b["slot_f1"]

    # (e) per-skill non-regression for the skills WS3 could cannibalize
    print("\n=== SKILL NON-REGRESSION (entity_card, org_departments) ===")
    def _ok(pairs, target):
        return sum(1 for ex, p in pairs if ex.family == "KG" and ex.skill == target and p.skill == target)
    def _tot(pairs, target):
        return sum(1 for ex, p in pairs if ex.family == "KG" and ex.skill == target)
    gate_e = True
    for target in ("entity_card", "org_departments"):
        b_ok, a_ok, tot = _ok(b_pairs, target), _ok(a_pairs, target), _tot(a_pairs, target)
        ok = a_ok >= b_ok
        gate_e = gate_e and ok
        print(f"  {target}: BEFORE {b_ok}/{tot} AFTER {a_ok}/{tot} [{'OK' if ok else 'REGRESS'}]")

    dt = time.time() - t0
    print(f"\n=== GATES ===  latency(all rows) {dt:.1f}s over {len(test)+len(hardneg)} rows")
    print(f"  (a) family non-regression : {'PASS' if gate_a else 'FAIL'}")
    print(f"  (b) regression paraphrases: {'PASS' if gate_b else 'FAIL'}")
    print(f"  (c) hardneg no new misfire: {'PASS' if gate_c else 'FAIL'}")
    print(f"  (d) slot-F1 improves      : {'DEFERRED' if gate_d is None else ('PASS' if gate_d else 'FAIL')}")
    print(f"  (e) skill non-regression  : {'PASS' if gate_e else 'FAIL'}")
    core = gate_a and gate_b and gate_c and gate_e
    print("MERGE GATE:", "PASS" if (core and gate_d is not False) else "FAIL")


if __name__ == "__main__":
    main()

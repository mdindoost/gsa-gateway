from __future__ import annotations
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.split import split
from v2.eval.router.arms import DetectorFirstArm, CoarseThenDeterministicArm, FullClassifierArm
from v2.eval.router.metrics import score


def run_bakeoff(examples, conn, encoder, test_frac=0.3, seed=0) -> dict:
    train, test = split(examples, encoder, test_frac=test_frac, seed=seed)
    fam_clf = ExemplarClassifier(level="family").fit(train, encoder)
    skill_clf = ExemplarClassifier(level="skill").fit(train, encoder)
    arms = {
        "detector_first": DetectorFirstArm(conn),
        "coarse_then_deterministic": CoarseThenDeterministicArm(conn, fam_clf, encoder),
        "full_classifier": FullClassifierArm(skill_clf, encoder),
    }
    result: dict = {"_meta": {"n_train": len(train), "n_test": len(test), "seed": seed}}
    for name, arm in arms.items():
        pairs = [(ex, arm.predict(ex.query)) for ex in test]
        result[name] = score(pairs)
    base = result["detector_first"]
    gate = {}
    for name in ("coarse_then_deterministic", "full_classifier"):
        m = result[name]
        rejected = (m["false_honest_partial"] > base["false_honest_partial"]
                    or m["wrong_confident_exact"] > base["wrong_confident_exact"])
        gate[name] = {"rejected": rejected,
                      "reason": "anti-fab leak above detector-first baseline" if rejected else "ok"}
    result["gate"] = gate
    return result


def format_report(result: dict) -> str:
    lines = ["# Kavosh v2.1 — Phase-0 Bake-off Report", "",
             f"train/test: {result['_meta']['n_train']}/{result['_meta']['n_test']} (seed {result['_meta']['seed']})", ""]
    for name in ("detector_first", "coarse_then_deterministic", "full_classifier"):
        m = result[name]
        lines += [f"## {name}",
                  f"- family_accuracy: {m['family_accuracy']:.3f}",
                  f"- skill_accuracy: {m['skill_accuracy']}",
                  f"- structured_false_negative: {m['structured_false_negative']}",
                  f"- false_honest_partial: {m['false_honest_partial']}  (anti-fab)",
                  f"- wrong_confident_exact: {m['wrong_confident_exact']}  (anti-fab)",
                  f"- gate: {result['gate'].get(name, {'reason':'baseline'})}", ""]
    return "\n".join(lines)

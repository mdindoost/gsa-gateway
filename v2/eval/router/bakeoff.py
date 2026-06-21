from __future__ import annotations
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.split import split, split_entity_disjoint
from v2.eval.router.arms import DetectorFirstArm, CoarseThenDeterministicArm, FullClassifierArm
from v2.eval.router.mask import MaskedEncoder
from v2.eval.router.abstain import AbstainingArm, calibrate_thresholds
from v2.eval.router.metrics import score


def _build_arms(conn, train, encoder, masker=None) -> dict:
    """The bake-off arms. Masked + abstaining arms are added only when a masker is supplied (CLI)."""
    fam_clf = ExemplarClassifier(level="family").fit(train, encoder)
    skill_clf = ExemplarClassifier(level="skill").fit(train, encoder)
    arms = {
        "detector_first": DetectorFirstArm(conn),
        "coarse_then_deterministic": CoarseThenDeterministicArm(conn, fam_clf, encoder),
        "full_classifier": FullClassifierArm(skill_clf, encoder),
    }
    if masker is not None:
        menc = MaskedEncoder(encoder, masker)
        m_fam = ExemplarClassifier(level="family").fit(train, menc)
        m_skill = ExemplarClassifier(level="skill").fit(train, menc)
        arms["masked_coarse"] = CoarseThenDeterministicArm(conn, m_fam, menc)
        arms["masked_full"] = FullClassifierArm(m_skill, menc)
        # abstention thresholds are calibrated on TRAIN only, then applied to masked_full
        _s, mgn = calibrate_thresholds(m_skill, train, menc, level="skill", target_precision=0.9)
        arms["masked_full_abstain"] = AbstainingArm(FullClassifierArm(m_skill, menc), margin_min=mgn)
    return arms


def run_bakeoff(examples, conn, encoder, test_frac=0.3, seed=0, masker=None,
                split_mode="paraphrase") -> dict:
    if split_mode == "entity":
        train, test = split_entity_disjoint(examples, test_frac=test_frac, seed=seed)
    else:
        train, test = split(examples, encoder, test_frac=test_frac, seed=seed)
    arms = _build_arms(conn, train, encoder, masker=masker)
    result: dict = {"_meta": {"n_train": len(train), "n_test": len(test), "seed": seed,
                              "split_mode": split_mode}}
    for name, arm in arms.items():
        pairs = [(ex, arm.predict(ex.query)) for ex in test]
        result[name] = score(pairs)
    base = result["detector_first"]
    gate = {}
    for name in arms:
        if name == "detector_first":
            continue
        m = result[name]
        rejected = (m["false_honest_partial"] > base["false_honest_partial"]
                    or m["wrong_confident_exact"] > base["wrong_confident_exact"])
        gate[name] = {"rejected": rejected,
                      "reason": "anti-fab leak above detector-first baseline" if rejected else "ok"}
    result["gate"] = gate
    return result


def format_report(result: dict, title: str = "Kavosh v2.1 — Phase-0 Bake-off Report") -> str:
    meta = result["_meta"]
    lines = [f"# {title}", "",
             f"split: {meta.get('split_mode', 'paraphrase')}-disjoint | "
             f"train/test: {meta['n_train']}/{meta['n_test']} (seed {meta['seed']})", ""]
    for name, m in result.items():
        if name in ("_meta", "gate"):
            continue
        lines += [f"## {name}",
                  f"- family_accuracy: {m['family_accuracy']:.3f}",
                  f"- skill_accuracy: {m['skill_accuracy']}",
                  f"- structured_false_negative: {m['structured_false_negative']}",
                  f"- false_honest_partial: {m['false_honest_partial']}  (anti-fab)",
                  f"- wrong_confident_exact: {m['wrong_confident_exact']}  (anti-fab)",
                  f"- gate: {result['gate'].get(name, {'reason': 'baseline'})}", ""]
    return "\n".join(lines)

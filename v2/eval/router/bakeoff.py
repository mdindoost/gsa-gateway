from __future__ import annotations
from v2.eval.router.classifier import ExemplarClassifier
from v2.eval.router.split import split, split_entity_disjoint
from v2.eval.router.arms import DetectorFirstArm, CoarseThenDeterministicArm, FullClassifierArm
from v2.eval.router.mask import MaskedEncoder
from v2.eval.router.abstain import AbstainingArm, calibrate_thresholds
from v2.eval.router.metrics import score


def _build_arms(conn, train, encoder, masker=None) -> tuple[dict, dict]:
    """The bake-off arms + a notes dict. Masked + abstaining arms are added only with a masker."""
    fam_clf = ExemplarClassifier(level="family").fit(train, encoder)
    skill_clf = ExemplarClassifier(level="skill").fit(train, encoder)
    arms = {
        "detector_first": DetectorFirstArm(conn),
        "coarse_then_deterministic": CoarseThenDeterministicArm(conn, fam_clf, encoder),
        "full_classifier": FullClassifierArm(skill_clf, encoder),
    }
    notes: dict = {}
    if masker is not None:
        menc = MaskedEncoder(encoder, masker)
        m_fam = ExemplarClassifier(level="family").fit(train, menc)
        m_skill = ExemplarClassifier(level="skill").fit(train, menc)
        arms["masked_coarse"] = CoarseThenDeterministicArm(conn, m_fam, menc)
        arms["masked_full"] = FullClassifierArm(m_skill, menc)
        # abstention thresholds are calibrated on TRAIN only, then applied to masked_full
        _s, mgn, met = calibrate_thresholds(m_skill, train, menc, level="skill", target_precision=0.9)
        arms["masked_full_abstain"] = AbstainingArm(FullClassifierArm(m_skill, menc), margin_min=mgn)
        notes["abstention_margin"] = round(mgn, 4)
        notes["abstention_target_met"] = met
    return arms, notes


def _partition(examples, encoder, test_frac, seed, split_mode):
    """Train/test partition that honors the labeling protocol: seeds are ALWAYS train (never gold),
    and an explicit `split:test` gold set (real-only) wins over a computed split. `hardneg` rows are
    excluded from the main run (scored as a separate suite). Falls back to a computed split over the
    real rows when no explicit gold set exists yet — with seeds still pinned to train (no seed/real
    paraphrase can straddle the boundary)."""
    seeds = [x for x in examples if x.provenance == "seed"]
    non_seed = [x for x in examples if x.provenance != "seed"]
    explicit_test = [x for x in non_seed if x.split == "test"]
    pool = [x for x in non_seed if x.split not in ("test", "hardneg")]
    if explicit_test:
        return seeds + pool, explicit_test
    if split_mode == "entity":
        tr, te = split_entity_disjoint(pool, test_frac=test_frac, seed=seed)
    else:
        tr, te = split(pool, encoder, test_frac=test_frac, seed=seed)
    return seeds + tr, te


def run_bakeoff(examples, conn, encoder, test_frac=0.3, seed=0, masker=None,
                split_mode="paraphrase") -> dict:
    train, test = _partition(examples, encoder, test_frac, seed, split_mode)
    arms, notes = _build_arms(conn, train, encoder, masker=masker)
    result: dict = {"_meta": {"n_train": len(train), "n_test": len(test), "seed": seed,
                              "split_mode": split_mode, **notes}}
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
    # honesty caveats — so the table is not over-read
    n_test = meta.get("n_test", 0)
    lines += ["> NOTES (read before trusting the numbers):",
              "> - coarse_* arms get their SKILL from the deterministic router (which resolves entities"
              " against the LIVE KG), not from the classifier — their skill_accuracy is the router's, and"
              " the deterministic arms enjoy a DB entity oracle the classifier arms do not.",
              f"> - small N (test={n_test}): single-digit anti-fab counts drive the gate; one row can flip"
              " a verdict — treat deltas as directional, not significant."]
    if "abstention_margin" in meta:
        margin, met = meta["abstention_margin"], meta.get("abstention_target_met")
        if not met:
            lines.append(f"> - ⚠ ABSTENTION DEGENERATE: target precision UNREACHABLE on TRAIN; fell back to"
                         f" max-precision margin={margin}. masked_full_abstain may equal masked_full.")
        elif margin == 0.0:
            lines.append("> - abstention inactive (not needed): TRAIN skill precision already meets target at"
                         " full coverage, so margin=0.0 and masked_full_abstain == masked_full.")
        else:
            lines.append(f"> - abstention active: calibrated margin={margin} (target precision met on TRAIN).")
    lines.append("")
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

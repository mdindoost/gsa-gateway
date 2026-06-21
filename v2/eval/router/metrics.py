from __future__ import annotations
from collections import Counter

TERMINAL_SKILLS = {"metric_of_person", "link_of_person"}


def score(pairs) -> dict:
    fam_ok = skill_ok = skill_n = 0
    struct_fn = fhp = wce = 0
    confusion: Counter = Counter()
    for gold, pred in pairs:
        if pred.family == gold.family:
            fam_ok += 1
        if gold.family == "KG":
            if pred.family != "KG":
                struct_fn += 1
            else:
                skill_n += 1
                if pred.skill == gold.skill:
                    skill_ok += 1
                else:
                    wce += 1                     # wrong-but-valid exact dispatch
                confusion[(gold.skill, pred.skill)] += 1
            # gold is NOT a terminal ask, but pred chose a terminal skill -> false honest-partial
            if gold.skill not in TERMINAL_SKILLS and pred.skill in TERMINAL_SKILLS:
                fhp += 1
    n = len(pairs) or 1
    return {
        "family_accuracy": fam_ok / n,
        "skill_accuracy": (skill_ok / skill_n) if skill_n else None,
        "structured_false_negative": struct_fn,
        "false_honest_partial": fhp,
        "wrong_confident_exact": wce,
        "confusion": dict(confusion),
    }

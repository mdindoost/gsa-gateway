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
        # false honest-partial: pred chose a terminal person skill but the gold is NOT a terminal
        # ask (any family). Catches the dangerous leak where a non-KG question is answered with a
        # confident "I don't have <person>'s metric/link" fabrication.
        gold_is_terminal_ask = (gold.family == "KG" and gold.skill in TERMINAL_SKILLS)
        if pred.skill in TERMINAL_SKILLS and not gold_is_terminal_ask:
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

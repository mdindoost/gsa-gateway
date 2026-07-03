# autoeval/checker.py
from __future__ import annotations
import re, sys
from pathlib import Path
from autoeval.models import ExpectedSpec, KavoshObservation, CheckOutcome

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval.faithfulness import _norm  # markdown/whitespace/casing-safe normalizer

def value_present(answer: str, value: str) -> bool:
    """The expected value appears in the answer after markdown/whitespace/casing normalization
    (`faithfulness._norm`). Uses normalized-substring rather than exact token-set membership,
    which was brittle when the value sat next to punctuation ('njit.edu.' vs 'njit.edu')."""
    nv = _norm(value)
    if not nv:
        return False
    return nv in _norm(answer)

def numeric_match(answer: str, value: str) -> bool:
    want = re.sub(r"[,\s]", "", str(value))
    nums = re.findall(r"\d[\d,]*", answer)
    return any(re.sub(r"[,\s]", "", n) == want for n in nums)

def list_overlap(answer: str, members: list[str]) -> tuple[float, float]:
    a = _norm(answer)
    found = [m for m in members if all(t in a for t in _norm(m).split())]
    recall = len(found) / len(members) if members else 0.0
    precision = 1.0 if found else 0.0  # coarse; recall is the meaningful signal for a roster
    return precision, recall

def check_typed(expected: ExpectedSpec, obs: KavoshObservation) -> bool | None:
    """True=answer correct, False=incorrect/contradiction, None=no typed check (prose -> soft judge)."""
    t = expected.type
    if t in ("contact", "entity") and expected.value:
        return value_present(obs.answer_text, expected.value)
    if t in ("count", "metric") and expected.value:
        return numeric_match(obs.answer_text, expected.value)
    if t == "list":
        _, recall = list_overlap(obs.answer_text, expected.members)
        return recall >= 0.6
    return None  # prose / abstain handled by the failure-class layer (Task 7)

def _asserts_a_value(obs: KavoshObservation) -> bool:
    """True iff the answer makes a CONCRETE factual assertion (email / phone / multi-digit number).
    Bare prose (even long) is NOT a concrete assertion — flagging it as fabrication produced ~50%
    false positives on real RAG answers. A canned/soft abstain is never an assertion."""
    if obs.is_abstain or obs.is_clarify:
        return False
    t = obs.answer_text
    if re.search(r"[\w.+-]+@[\w-]+\.\w+", t):            # an email
        return True
    if re.search(r"\d{3}[.\-\s]?\d{3}[.\-\s]?\d{4}", t):  # a phone
        return True
    if re.search(r"\b\d{3,}\b", t):                       # a 3+ digit figure
        return True
    return False

def classify(expected: ExpectedSpec, obs: KavoshObservation, arm: str,
             missing_fields: list[str], twin_passed: bool | None) -> CheckOutcome:
    ev = {"expected_type": expected.type, "expected_value": expected.value,
          "answer_snippet": obs.answer_text[:240], "family": obs.family, "skill": obs.skill,
          "resolved_key": obs.resolved_key, "is_abstain": obs.is_abstain}
    field_missing = bool(expected.missing_field and expected.missing_field in missing_fields)

    # --- Arm C: should abstain/clarify ---
    if expected.type == "abstain_or_clarify" or arm == "out_of_scope":
        if obs.is_abstain or obs.is_clarify:
            return CheckOutcome("pass", None, field_missing, ev)  # correct abstain (maybe data_gap)
        if _asserts_a_value(obs):
            ev["check"] = "armC_assertion"
            return CheckOutcome("fail", "fabrication", field_missing, ev)
        # substantive prose, no concrete value: provisional pass, routed to the soft judge for audit
        return CheckOutcome("pass", None, field_missing, ev, graded_soft=True)

    # --- Arm A/B: should answer ---
    typed = check_typed(expected, obs)
    ev["check"] = f"typed:{expected.type}"
    if typed is None:
        # prose/fuzzy -> soft judge decides; hard result is a provisional pass, graded_soft set later
        return CheckOutcome("pass", None, field_missing, ev, graded_soft=True)
    if typed is True:
        return CheckOutcome("pass", None, field_missing, ev)
    # typed is False: either a contradiction (asserted a wrong value) or a miss (abstained/absent)
    if _asserts_a_value(obs):
        ev["check"] = "contradiction"
        return CheckOutcome("fail", "fabrication", field_missing or False, ev)
    # a miss (no value asserted). Arm B whose clean twin passed -> resolution broke on noise.
    if arm == "noisy" and twin_passed is True and not obs.slot_extracted:
        return CheckOutcome("fail", "resolution_failure", False, ev)
    return CheckOutcome("fail", "routing_failure", False, ev)

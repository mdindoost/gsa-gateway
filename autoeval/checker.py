# autoeval/checker.py
from __future__ import annotations
import re, sys
from pathlib import Path
from autoeval.models import ExpectedSpec, KavoshObservation, CheckOutcome

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval.faithfulness import _norm  # markdown/whitespace/casing-safe normalizer

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
_STRIP = ".,;:!?()[]{}\"'"

def _toks(s: str) -> list[str]:
    """Normalized tokens with leading/trailing punctuation stripped (keeps internal dots so
    'njit.edu' stays one token but 'njit.edu.' at a sentence end matches it)."""
    return [t for t in (w.strip(_STRIP) for w in _norm(s).split()) if t]

def value_present(answer: str, value: str) -> bool:
    """Every content token of the expected value appears in the answer — order-independent and
    trailing-punctuation-robust. This fixes BOTH 'njit.edu.' vs 'njit.edu' (punctuation) AND
    multi-word values the answer interleaves (e.g. '569 Weston Hall (WEST)' vs
    '569 Weston Hall, which is on the WEST side'), which a contiguous substring missed."""
    vt = _toks(value)
    if not vt:
        return False
    at = set(_toks(answer))
    return all(t in at for t in vt)

def numeric_match(answer: str, value: str) -> bool:
    want = re.sub(r"[,\s]", "", str(value))
    nums = re.findall(r"\d[\d,]*", answer)
    return any(re.sub(r"[,\s]", "", n) == want for n in nums)

def _member_found(member: str, answer_tokens: set[str]) -> bool:
    """A roster/list member counts as found when >=60% of its content tokens appear in the answer
    (token-level, so an awkwardly-phrased expected member like 'I am primarily interested in
    sustainability' still matches an answer that says 'sustainability')."""
    mt = _toks(member)
    if not mt:
        return False
    hit = sum(1 for t in mt if t in answer_tokens)
    return hit / len(mt) >= 0.6

def list_overlap(answer: str, members: list[str]) -> tuple[float, float]:
    at = set(_toks(answer))
    found = [m for m in members if _member_found(m, at)]
    recall = len(found) / len(members) if members else 0.0
    precision = 1.0 if found else 0.0  # coarse; recall is the meaningful signal for a roster
    return precision, recall

def check_typed(expected: ExpectedSpec, obs: KavoshObservation) -> bool | None:
    """True=answer contains the expected value, False=not found, None=no typed check (prose->soft)."""
    t = expected.type
    if t in ("contact", "entity") and expected.value:
        return value_present(obs.answer_text, expected.value)
    if t in ("count", "metric") and expected.value:
        return numeric_match(obs.answer_text, expected.value)
    if t == "list":
        _, recall = list_overlap(obs.answer_text, expected.members)
        return recall >= 0.6
    return None  # prose / abstain handled below

def _answer_emails(text: str) -> set[str]:
    return {_norm(e) for e in _EMAIL_RE.findall(text)}

def classify(expected: ExpectedSpec, obs: KavoshObservation, arm: str,
             missing_fields: list[str], twin_passed: bool | None) -> CheckOutcome:
    """Deterministic classification. Hard `fabrication` fires ONLY on a VERIFIABLE contradiction
    (an email in the answer that differs from the known email, or a contact value asserted for a
    field we KNOW is absent). Everything ambiguous (topical prose, unmatched non-contact value)
    routes to the soft LLM-judge (graded_soft) or to a routing/resolution miss — never to a false
    fabrication. This keeps the fabrication list precise enough to trust and act on."""
    ev = {"expected_type": expected.type, "expected_value": expected.value,
          "answer_snippet": obs.answer_text[:240], "family": obs.family, "skill": obs.skill,
          "resolved_key": obs.resolved_key, "is_abstain": obs.is_abstain}
    field_missing = bool(expected.missing_field and expected.missing_field in missing_fields)

    # --- Arm C: should abstain/clarify ---
    if expected.type == "abstain_or_clarify" or arm == "out_of_scope":
        if obs.is_abstain or obs.is_clarify:
            return CheckOutcome("pass", None, field_missing, ev)  # correct abstain (maybe data_gap)
        # Deterministic Arm-C fabrication ONLY when we KNOW the targeted field is absent and the
        # answer asserts a value of that field's type (an email for a missing email). We cannot
        # deterministically judge a topical over-answer ("who is the director" -> EOP history), so
        # that goes to the soft judge instead of a false fabrication.
        if field_missing and expected.missing_field == "email" and _EMAIL_RE.search(obs.answer_text):
            ev["check"] = "armC_missing_email_fabricated"
            return CheckOutcome("fail", "fabrication", True, ev)
        return CheckOutcome("pass", None, field_missing, ev, graded_soft=True)

    # --- Arm A/B: should answer ---
    typed = check_typed(expected, obs)
    ev["check"] = f"typed:{expected.type}"
    if typed is None:
        return CheckOutcome("pass", None, field_missing, ev, graded_soft=True)  # prose -> soft judge
    if typed is True:
        return CheckOutcome("pass", None, field_missing, ev)
    # typed False: the expected value wasn't found. The ONLY deterministic contradiction we can
    # assert is an email one: the answer states an email, but not the expected one.
    if expected.value and "@" in expected.value:
        emails = _answer_emails(obs.answer_text)
        if emails and _norm(expected.value) not in emails:
            ev["check"] = "email_contradiction"
            return CheckOutcome("fail", "fabrication", False, ev)
    # Otherwise it's a miss (absent / wrong / topical). Arm-B whose clean twin passed => noise broke
    # resolution; else a routing/answerability miss.
    if arm == "noisy" and twin_passed is True and not obs.slot_extracted:
        return CheckOutcome("fail", "resolution_failure", False, ev)
    return CheckOutcome("fail", "routing_failure", False, ev)

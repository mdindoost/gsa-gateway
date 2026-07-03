# autoeval/checker.py
from __future__ import annotations
import re, sys
from pathlib import Path
from autoeval.models import ExpectedSpec, KavoshObservation, CheckOutcome

sys.path.insert(0, str(Path("/home/md724/gsa-gateway")))
from v2.core.retrieval.faithfulness import _norm  # markdown/whitespace/casing-safe normalizer

def value_present(answer: str, value: str) -> bool:
    """All normalized tokens of the expected value appear in the normalized answer.
    Reuses faithfulness._norm so markdown ** and casing don't break the match (the WS4 fix)."""
    a_tokens = set(_norm(answer).split())
    v_tokens = _norm(value).split()
    if not v_tokens:
        return False
    return all(t in a_tokens for t in v_tokens)

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

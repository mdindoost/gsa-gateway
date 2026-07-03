"""Deterministic follow-up matcher — maps a user's reply to one of the pending options.
NO LLM: affirmation/negation are closed lexicons matched against the WHOLE message; a
pick-1-of-N is resolved by ordinal or a UNIQUE label match. Anything ambiguous returns
None (route normally) — never a guess."""

import re

DECLINE = object()   # sentinel: an explicit "no"

_AFFIRM = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "yes please",
    "please do", "do it", "go ahead", "sounds good", "yes do it",
}
_NEGATE = {"no", "nope", "nah", "never mind", "nevermind", "no thanks", "no thank you"}

_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.strip(".!?,;:'\"")
    return re.sub(r"\s+", " ", t)


def _ordinal_index(norm: str, n: int):
    # "the first", "first", "option 2", "#3", bare "2", "2nd"
    m = re.fullmatch(r"(?:the\s+|option\s+|#)?(\d+)(?:st|nd|rd|th)?", norm)
    if m:
        i = int(m.group(1))
        return i - 1 if 1 <= i <= n else None
    m = re.fullmatch(r"(?:the\s+|option\s+)?([a-z]+)(?:\s+one)?", norm)
    if m and m.group(1) in _ORDINALS:
        i = _ORDINALS[m.group(1)]
        return i - 1 if 1 <= i <= n else None
    return None


def match_followup(text: str, options):
    """Return the selected option index, DECLINE (explicit no), or None (no recognized selection)."""
    if not options:
        return None
    norm = _normalize(text)
    if not norm:
        return None
    if norm in _NEGATE:
        return DECLINE
    if norm in _AFFIRM:
        return 0 if len(options) == 1 else None   # bare "yes" to N options is ambiguous
    # ordinal and label are both candidate selections; reconcile — never guess on a collision.
    idx_ord = _ordinal_index(norm, len(options))
    labels = [_normalize(o.label) for o in options]
    exact = [i for i, lbl in enumerate(labels) if lbl == norm]
    subs = [i for i, lbl in enumerate(labels) if norm in lbl]
    idx_lbl = exact[0] if len(exact) == 1 else (subs[0] if len(subs) == 1 else None)
    if idx_ord is not None and idx_lbl is not None and idx_ord != idx_lbl:
        return None                      # ordinal and label disagree → ambiguous, never guess
    if idx_ord is not None:
        return idx_ord
    return idx_lbl

"""Nugget self-containment gate (Fable pronoun ruling, 2026-07-07).

A nugget whose grammatical SUBJECT is an unresolved anaphor ("He…", "This program…", "The
department…") cannot be judged in isolation: a human silently resolves it from the question stem,
the NLI judge cannot, producing SYSTEMATIC (not noisy) machine–human disagreement that depresses κ.
Such nuggets are excluded from the κ denominator + headline debt and reported as a counted
"non-self-contained" bucket. This is the minimal mechanical gate; full pronoun-resolving nuggetizer
is deferred.
"""
from __future__ import annotations
import re

# Pure pronominal subjects — always anaphoric.
_PRONOUN_OPENERS = {"he", "she", "his", "her", "him", "it", "its",
                    "they", "their", "them"}
# Demonstratives used as an opener subject — anaphoric ("This program", "These courses").
_DEMONSTRATIVES = {"this", "that", "these", "those"}


def is_self_contained(fact: str) -> bool:
    """False iff the nugget opens with an unresolved anaphoric subject."""
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]+", fact or "")
    if not tokens:
        return True                      # numbers-only / empty -> judgeable as-is
    w0 = tokens[0].lower()
    if w0 in _PRONOUN_OPENERS or w0 in _DEMONSTRATIVES:
        return False
    if w0 == "the":
        # "The <common noun>" is anaphoric; "The <Proper/ACRONYM>" names its subject.
        if len(tokens) >= 2 and tokens[1][0].islower():
            return False
    return True

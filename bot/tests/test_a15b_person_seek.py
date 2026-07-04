"""A15b — the person-seeking predicate (router.is_person_seeking) that scopes the compose guard.

Must fire on "identify/list faculty who do X" and NOT on policy questions that merely mention
faculty, nor on contact/office/how-to shapes (the big false-positive class).
"""
import pytest

from v2.core.retrieval.router import is_person_seeking


@pytest.mark.parametrize("q", [
    "which professors study the brain",
    "what faculty work on swarm robotics",
    "who studies memory and cognition",
    "who researches the gut microbiome",
    "faculty studying soft matter physics",
    "professors who research misinformation",
    "which researchers work on earthquakes",
])
def test_person_seeking_true(q):
    assert is_person_seeking(q) is True


@pytest.mark.parametrize("q", [
    "how do professors get tenure",              # policy mentioning professors
    "how can I email a professor",               # contact/how-to
    "who do I contact about advising",           # contact
    "professor office hours",                    # office
    "what is the tuition for the phd",           # non-person
    "how do I apply for financial aid",          # non-person
    "whose office handles parking",              # office
])
def test_person_seeking_false(q):
    assert is_person_seeking(q) is False

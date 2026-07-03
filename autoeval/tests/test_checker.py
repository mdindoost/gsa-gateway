# autoeval/tests/test_checker.py
from autoeval.models import ExpectedSpec, KavoshObservation
from autoeval.checker import value_present, numeric_match, list_overlap

def _obs(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

def test_value_present_markdown_and_case_insensitive():
    assert value_present("Her email is **JDOE@njit.edu**.", "jdoe@njit.edu")
    assert not value_present("Her email is someoneelse@njit.edu.", "jdoe@njit.edu")

def test_numeric_match():
    assert numeric_match("He has 1,234 citations.", "1234")
    assert not numeric_match("He has 999 citations.", "1234")

def test_list_overlap_precision_recall():
    p, r = list_overlap("Members: Alice, Bob, Carol", ["Alice", "Bob", "Dan"])
    assert r == 2/3  # Alice+Bob found of 3 expected

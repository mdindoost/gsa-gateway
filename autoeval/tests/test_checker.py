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

from autoeval.checker import classify
from autoeval.models import ExpectedSpec, KavoshObservation

def _o(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

def test_armC_confident_answer_is_fabrication():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/zzyzx")
    obs = _o("Professor Zzyzx's email is zzyzx@njit.edu", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "fabrication"

def test_armC_correct_abstain_passes():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/zzyzx")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "pass" and out.failure_class is None

def test_missing_field_correct_abstain_is_data_gap_not_routing():
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/x", missing_field="phone")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=["phone"], twin_passed=None)
    assert out.result == "pass" and out.data_gap is True and out.failure_class is None

def test_armA_contradiction_is_fabrication():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu",
                       must_contain_field="email")
    obs = _o("Her email is wrong@njit.edu")   # asserts a value, but not the truth
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "fabrication"

def test_armA_miss_is_routing_failure():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None)
    assert out.result == "fail" and out.failure_class == "routing_failure"

def test_armB_fail_with_passing_twin_is_resolution_failure():
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True)
    out = classify(exp, obs, arm="noisy", missing_fields=[], twin_passed=True)
    assert out.failure_class == "resolution_failure"

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

def test_email_present_subdomain_whole_address():
    from autoeval.checker import email_present
    # multi-label domain must not be truncated (would false-negative a correct answer)
    assert email_present("Reach him at jdoe@cs.njit.edu today.", "jdoe@cs.njit.edu")
    assert not email_present("Reach him at jdoe@njit.edu today.", "jdoe@cs.njit.edu")

def test_numeric_match():
    assert numeric_match("He has 1,234 citations.", "1234")
    assert not numeric_match("He has 999 citations.", "1234")

def test_numeric_match_spelled_out_small_ints():
    # Kavosh says "one title", not "1 title" — a correct count must not read as a miss.
    assert numeric_match("She is listed as having one title: Events Coordinator.", "1")
    assert numeric_match("There are three research areas.", "3")
    assert not numeric_match("There are two areas.", "3")      # wrong word-number still fails
    assert not numeric_match("He has 4203490 citations.", "1")  # no spurious word match on big metrics

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

def test_armC_missing_email_fabricated_is_fabrication():
    # We KNOW the email field is absent, so asserting an email in THIS person's address family
    # (jdoe / john.doe) is a verifiable fabrication.
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/x", missing_field="email")
    obs = _o("His email is john.doe@njit.edu", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=["email"], twin_passed=None,
                   subject_name="John Doe")
    assert out.result == "fail" and out.failure_class == "fabrication" and out.data_gap is True

def test_armC_honest_redirect_email_is_not_fabrication():
    # Correctly withholds the person's (missing) email and offers a real, UNRELATED office contact.
    # The office email is not in the person's address family => not a fabrication, routes to soft.
    exp = ExpectedSpec(type="abstain_or_clarify", item_key="crawler/x", missing_field="email")
    obs = _o("I don't see a direct email for her, but you can reach the department office at "
             "gsa-pres@njit.edu.", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=["email"], twin_passed=None,
                   subject_name="Jane Doe")
    assert out.result == "pass" and out.failure_class is None and out.graded_soft is True

def test_armA_wrong_email_with_scattered_true_tokens_is_fabrication():
    # The true email's local-part ("jdoe") and domain ("njit.edu") both appear as separate tokens,
    # but the answer asserts a DIFFERENT whole email. Whole-address matching must NOT be fooled.
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("The chair is jdoe, but per njit.edu her email is actually mchen@njit.edu.")
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None, subject_name="Jane Doe")
    assert out.result == "fail" and out.failure_class == "fabrication"

def test_armB_fail_via_slot_extraction_is_routing_not_resolution():
    # Passing clean twin, but the noisy route went through LLM slot extraction => fidelity caveat,
    # so it's a routing_failure, not credited as clean-twin resolution breakage.
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="jdoe@njit.edu")
    obs = _o("I wasn't able to find specific information about that in the GSA knowledge base.",
             is_abstain=True, slot_extracted=True)
    out = classify(exp, obs, arm="noisy", missing_fields=[], twin_passed=True, subject_name="Jane Doe")
    assert out.failure_class == "routing_failure"

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

def test_armC_noncanned_soft_refusal_is_not_fabrication():
    exp = ExpectedSpec("abstain_or_clarify", "crawler/zzyzx")
    obs = _o("I'm not sure I have that information readily available for you right now, "
             "sorry about that.", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "pass" and out.failure_class is None

def test_armC_email_in_prose_without_known_missing_field_is_soft_not_fabrication():
    # An out-of-scope answer that mentions a (possibly real) email but where we have NO ground-truth
    # 'this field is absent' cannot be deterministically called a fabrication -> soft judge decides.
    exp = ExpectedSpec("abstain_or_clarify", "crawler/x")
    obs = _o("His office hours aren't listed, but you can reach him at prof@njit.edu.", is_abstain=False)
    out = classify(exp, obs, arm="out_of_scope", missing_fields=[], twin_passed=None)
    assert out.result == "pass" and out.failure_class is None and out.graded_soft is True

def test_armA_office_value_interleaved_in_prose_passes():
    # Multi-word value the answer splits across a clause must still match (order-independent tokens).
    exp = ExpectedSpec(type="contact", item_key="crawler/x", value="569 Weston Hall (WEST)")
    obs = _o("His office is in room 569 Weston Hall, which is on the WEST side. Email: a@njit.edu.")
    out = classify(exp, obs, arm="answer", missing_fields=[], twin_passed=None)
    assert out.result == "pass" and out.failure_class is None

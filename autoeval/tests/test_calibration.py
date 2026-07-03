"""Spec §9 trust gate: feed the checker hand-labeled answers, confirm each failure_class.
This is the instrument calibration — it must pass before enabling the long run."""
from autoeval.models import ExpectedSpec, KavoshObservation
from autoeval.checker import classify

def _o(text, **kw):
    d = dict(used_ai=False, is_live=False, is_deep=False, source_note=None, family="KG",
             skill="contact_of_person", resolved_key="crawler/x", slot_extracted=False,
             is_abstain=False, is_clarify=False, latency_ms=1)
    d.update(kw); return KavoshObservation(answer_text=text, **d)

CASES = [
    # (expected, obs, arm, missing_fields, twin_passed, want_result, want_class, want_datagap)
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("Jane's email is **jdoe@njit.edu**."), "answer", [], None, "pass", None, False),
    # Arm-A email MISMATCH (a different email asserted) => verifiable fabrication.
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("Jane's email is bad@njit.edu."), "answer", [], None, "fail", "fabrication", False),
    # Arm-C: email fabricated for a person whose email we KNOW is absent => verifiable fabrication.
    (ExpectedSpec("abstain_or_clarify", "crawler/x", missing_field="email"),
     _o("Their email is invented@njit.edu"), "out_of_scope", ["email"], None, "fail", "fabrication", True),
    (ExpectedSpec("abstain_or_clarify", "crawler/x", missing_field="phone"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "out_of_scope", ["phone"], None, "pass", None, True),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "noisy", [], True, "fail", "resolution_failure", False),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "answer", [], None, "fail", "routing_failure", False),
    # --- smoke-run regressions (Task 15): real answers the checker MUST get right ---
    # correct email at a sentence boundary
    (ExpectedSpec("contact", "people.njit.edu/profile/schuman", value="anthony.w.schuman@njit.edu"),
     _o("Anthony Schuman's email address is anthony.w.schuman@njit.edu. You can reach him."),
     "answer", [], None, "pass", None, False),
    # correct multi-word office value the answer interleaves across a clause
    (ExpectedSpec("contact", "people.njit.edu/profile/schuman", value="569 Weston Hall (WEST)"),
     _o("Anthony Schuman's office is in room 569 Weston Hall, which is on the WEST side. "
        "His email is anthony.w.schuman@njit.edu."),
     "answer", [], None, "pass", None, False),
    # correct research-area list even though one expected member is awkwardly phrased
    (ExpectedSpec("list", "31430", members=["food supply chains", "food waste",
                  "I am primarily interested in sustainability",
                  "omnichannel retail operations with a specific interest in grocery retailing"]),
     _o("Jae-Hyuck Park's research areas include food supply chains, food waste, and sustainability. "
        "They are particularly interested in omnichannel retail operations with a focus on grocery retailing."),
     "answer", [], None, "pass", None, False),
    # topical prose to an unanswerable question (no verifiable false fact) => soft judge, not fabrication
    (ExpectedSpec("abstain_or_clarify", "58"),
     _o("The Educational Opportunity Program offers academic support and personal counseling to students."),
     "out_of_scope", [], None, "pass", None, False),
    # Arm-A prose miss (value absent, no conflicting email) => routing, not fabrication
    (ExpectedSpec("contact", "x", value="jdoe@njit.edu"),
     _o("I can share general information about the department, but I don't have that detail."),
     "answer", [], None, "fail", "routing_failure", False),
]

def test_calibration_matrix():
    for exp, obs, arm, miss, twin, wr, wc, wg in CASES:
        out = classify(exp, obs, arm, miss, twin)
        assert (out.result, out.failure_class, out.data_gap) == (wr, wc, wg), (exp.type, arm, out)

def test_armC_topical_prose_no_value_is_graded_soft():
    """Arm-C prose with no concrete value is a provisional pass routed to the soft judge, not a
    silent hard pass — it's auditable rather than assumed correct."""
    exp = ExpectedSpec("abstain_or_clarify", "58")
    obs = _o("The Educational Opportunity Program offers academic support and personal counseling "
             "to students.")
    out = classify(exp, obs, "out_of_scope", [], None)
    assert out.result == "pass" and out.failure_class is None and out.graded_soft is True

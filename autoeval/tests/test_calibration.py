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
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("Jane's email is bad@njit.edu."), "answer", [], None, "fail", "fabrication", False),
    (ExpectedSpec("abstain_or_clarify", "crawler/zzyzx"),
     _o("Prof Zzyzx's email is z@njit.edu"), "out_of_scope", [], None, "fail", "fabrication", False),
    (ExpectedSpec("abstain_or_clarify", "crawler/x", missing_field="phone"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "out_of_scope", ["phone"], None, "pass", None, True),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "noisy", [], True, "fail", "resolution_failure", False),
    (ExpectedSpec("contact", "crawler/x", value="jdoe@njit.edu"),
     _o("I wasn't able to find specific information about that in the GSA knowledge base.",
        is_abstain=True), "answer", [], None, "fail", "routing_failure", False),
]

def test_calibration_matrix():
    for exp, obs, arm, miss, twin, wr, wc, wg in CASES:
        out = classify(exp, obs, arm, miss, twin)
        assert (out.result, out.failure_class, out.data_gap) == (wr, wc, wg), (exp.type, arm, out)

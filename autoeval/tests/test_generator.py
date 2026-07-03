# autoeval/tests/test_generator.py
from autoeval.models import SourceItem
from autoeval.generator import parse_and_validate

def _item():
    return SourceItem(item_type="person", item_key="crawler/x", display_name="Jane Doe",
                      ground_truth={"email": "jdoe@njit.edu"}, has_fields=["email"],
                      missing_fields=["phone"])

def test_validate_keeps_checkable_and_drops_specless():
    raw = {"questions": [
        {"arm": "answer", "question": "email?",
         "expected": {"type": "contact", "value": "jdoe@njit.edu", "must_contain_field": "email"}},
        {"arm": "answer", "question": "bad", "expected": {"type": "contact"}},  # no value -> dropped
        {"arm": "out_of_scope", "question": "zzyzx?",
         "expected": {"type": "abstain_or_clarify"}},
    ]}
    qs = parse_and_validate(raw, _item())
    kinds = [(q.arm, q.expected.type) for q in qs]
    assert ("answer", "contact") in kinds
    assert ("out_of_scope", "abstain_or_clarify") in kinds
    assert len(qs) == 2  # the value-less contact question was dropped

def test_expected_item_key_is_forced_from_item():
    raw = {"questions": [{"arm": "answer", "question": "q",
            "expected": {"type": "contact", "value": "jdoe@njit.edu"}}]}
    qs = parse_and_validate(raw, _item())
    assert qs[0].expected.item_key == "crawler/x"  # never trusts Codex for the key

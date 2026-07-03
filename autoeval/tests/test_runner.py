from autoeval.runner import detect_abstain, detect_clarify, resolved_key_for

class _D:  # stand-in for RouteDecision
    def __init__(self, family, skill, args): self.family, self.skill, self.args = family, skill, args

def test_detect_abstain_matches_canned():
    assert detect_abstain("I wasn't able to find specific information about that in the GSA knowledge base.")
    assert detect_abstain("I wasn't able to find a specific answer to that in the GSA knowledge base.")
    assert not detect_abstain("Jane Doe's email is jdoe@njit.edu")

def test_detect_clarify():
    assert detect_clarify("I want to make sure I answer the right thing — could you rephrase")
    assert not detect_clarify("Here is the answer.")

def test_resolved_key_person_vs_org():
    k, slot = resolved_key_for(_D("KG", "contact_of_person", {"entity_id": "crawler/x"}))
    assert k == "crawler/x" and slot is False
    k2, _ = resolved_key_for(_D("KG", "people_in_org", {"org_id": 42}))
    assert k2 == "42"
    k3, _ = resolved_key_for(_D("RAG", None, {}))
    assert k3 is None
    # orgs_by_type carries parent_org_id, not org_id — was None before the allowlist-free fix.
    k4, _ = resolved_key_for(_D("KG", "orgs_by_type", {"org_type": "department", "parent_org_id": 7}))
    assert k4 == "7"
    # org_departments was absent from the old allowlist entirely.
    k5, _ = resolved_key_for(_D("KG", "org_departments", {"org_id": 42}))
    assert k5 == "42"
    k6, _ = resolved_key_for(_D("KG", "people_by_area_tag", {"area": "ml", "org_id": 9}))
    assert k6 == "9"

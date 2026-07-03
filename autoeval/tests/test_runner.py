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

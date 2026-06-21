import numpy as np
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.mask import SlotMasker, MaskedEncoder, ORG, PERSON


def test_masker_replaces_org_and_person_longest_first():
    m = SlotMasker(org_terms=["computer science", "cs", "mechanical engineering"],
                   person_terms=["Ioannis Koutis", "Koutis"])
    assert m.mask("who teaches in computer science") == f"who teaches in {ORG}"
    assert m.mask("faculty in CS") == f"faculty in {ORG}"
    assert m.mask("what does Ioannis Koutis research") == f"what does {PERSON} research"
    assert m.mask("Koutis citation") == f"{PERSON} citation"


def test_masker_respects_word_boundaries():
    m = SlotMasker(org_terms=["cs"], person_terms=[])
    assert m.mask("physics is hard") == "physics is hard"   # 'cs' not matched inside 'physics'


def test_masked_encoder_equals_premasked():
    enc = FakeEncoder(16)
    me = MaskedEncoder(enc, SlotMasker(org_terms=["cs"], person_terms=[]))
    a = me(["faculty in cs"])
    b = enc([f"faculty in {ORG}"])
    assert np.allclose(a, b)

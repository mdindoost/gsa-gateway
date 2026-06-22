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


def test_no_sentinel_re_match_from_pathological_slug():
    # a future org slug literally equal to the sentinel bare word must not corrupt emitted sentinels
    m = SlotMasker(org_terms=["org", "computer science"], person_terms=[])
    assert m.mask("who teaches in computer science") == f"who teaches in {ORG}"


def test_multiword_org_inside_sentence_with_punctuation():
    m = SlotMasker(org_terms=["mechanical engineering"], person_terms=[])
    assert m.mask("top 10 by citations in mechanical engineering, please") == \
        f"top 10 by citations in {ORG}, please"


def test_empty_term_lists_are_noop():
    m = SlotMasker(org_terms=[], person_terms=[])
    assert m.mask("who teaches in cs") == "who teaches in cs"

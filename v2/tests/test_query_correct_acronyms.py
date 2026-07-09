from v2.core.retrieval.query_correct import augment_acronyms

def test_augment_keeps_bare_and_appends():
    assert augment_acronyms("what is gsa") == "what is gsa graduate student association"

def test_augment_metric_words():
    # the metric class the dictionary owns (spec §14.1)
    assert augment_acronyms("top cited prof in computer sci") == \
        "top cited prof professor in computer sci science"

def test_augment_noop_when_no_abbrev():
    assert augment_acronyms("who is the dean of engineering") == "who is the dean of engineering"

def test_augment_case_insensitive_preserves_bare():
    assert augment_acronyms("What is GSA").lower() == "what is gsa graduate student association"

def test_augment_skips_protected():
    assert augment_acronyms("prof wang", protected={"prof"}) == "prof wang"

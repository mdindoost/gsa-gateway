from v2.core.retrieval.query_correct import ACRONYMS, augment_acronyms


def test_augment_keeps_bare_and_appends():
    assert augment_acronyms("dept chair") == "dept department chair"


def test_augment_metric_words():
    # the metric class the dictionary owns (spec §14.1): sci->science, prof->professor
    assert augment_acronyms("top cited prof in computer sci") == \
        "top cited prof professor in computer sci science"


def test_augment_noop_when_no_abbrev():
    assert augment_acronyms("who is the dean of engineering") == "who is the dean of engineering"


def test_augment_case_insensitive_preserves_bare():
    assert augment_acronyms("Which DEPT").lower() == "which dept department"


def test_augment_skips_protected():
    assert augment_acronyms("prof wang", protected={"prof"}) == "prof wang"


def test_org_slug_acronyms_are_not_expanded():
    """REGRESSION LOCK (route-diff gate): the dictionary must NEVER expand a token the router
    resolves natively as an org slug/alias — expanding it breaks the native org resolution and
    demotes a correct structured route into RAG. gsa/cs/ece must pass through UNCHANGED."""
    assert "gsa" not in ACRONYMS
    assert "cs" not in ACRONYMS
    assert "ece" not in ACRONYMS
    assert augment_acronyms("gsa president") == "gsa president"
    assert augment_acronyms("who are the gsa officers") == "who are the gsa officers"
    assert augment_acronyms("who run cs") == "who run cs"
    assert augment_acronyms("ece faculty") == "ece faculty"

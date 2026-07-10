from facultyfolio import render


ROLLUP = {"nsf": 37401075, "nih": 6076611, "n_funded": 36, "as_of": "2026-07-10"}
ROLLUP_NSF_ONLY = {"nsf": 6958743, "nih": 0, "n_funded": 7, "as_of": "2026-07-10"}


def test_rollup_view_both_agencies():
    v = render._rollup_view(ROLLUP)
    assert v["parts"] == [("$37.40M", "NSF"), ("$6.08M", "NIH")]
    assert v["n"] == 36
    assert v["as_of"] == "Jul 2026"


def test_rollup_view_omits_zero_agency():
    v = render._rollup_view(ROLLUP_NSF_ONLY)
    assert v["parts"] == [("$6.96M", "NSF")]


def test_rollup_view_none():
    assert render._rollup_view(None) is None
    assert render._rollup_view({"nsf": 0, "nih": 0, "n_funded": 0, "as_of": None}) is None

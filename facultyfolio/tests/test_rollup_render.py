from facultyfolio import render

ROLL = {"nsf_awards": 59, "nsf_active": 14, "nih_projects": 5, "nih_active": 1,
        "funded": 23, "as_of": "2026-07-10"}
ROLL_NSF_ONLY = {"nsf_awards": 17, "nsf_active": 8, "nih_projects": 0, "nih_active": 0,
                 "funded": 7, "as_of": "2026-07-10"}


def test_rollup_view_both_agencies_counts():
    v = render._rollup_view(ROLL)
    assert v["parts"] == [("59 awards (14 active)", "NSF"), ("5 projects (1 active)", "NIH")]
    assert v["funded"] == 23
    assert v["as_of"] == "Jul 2026"
    assert "$" not in repr(v)


def test_rollup_view_omits_zero_agency():
    v = render._rollup_view(ROLL_NSF_ONLY)
    assert v["parts"] == [("17 awards (8 active)", "NSF")]


def test_rollup_view_singular_and_none():
    one = render._rollup_view({"nsf_awards": 1, "nsf_active": 0, "nih_projects": 0,
                               "nih_active": 0, "funded": 1, "as_of": None})
    assert one["parts"] == [("1 award (0 active)", "NSF")]
    assert one["as_of"] == ""
    assert render._rollup_view(None) is None
    assert render._rollup_view({"nsf_awards": 0, "nih_projects": 0, "funded": 0, "as_of": None}) is None

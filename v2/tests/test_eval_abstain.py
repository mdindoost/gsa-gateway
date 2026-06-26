from scripts.eval_abstain import score_abstain


def test_all_deflect_is_perfect():
    recs = [{"q": "a", "class": "deflect"}, {"q": "b", "class": "deflect"}]
    s = score_abstain(recs)
    assert s["total"] == 2 and s["deflected"] == 2 and s["answered"] == 0
    assert s["rate"] == 1.0 and s["leaks"] == []


def test_leaks_are_listed():
    recs = [
        {"q": "a", "class": "deflect"},
        {"q": "b", "class": "kb"},      # forced answer = leak
        {"q": "c", "class": "live"},    # forced answer = leak
    ]
    s = score_abstain(recs)
    assert s["total"] == 3 and s["deflected"] == 1 and s["answered"] == 2
    assert abs(s["rate"] - 1/3) < 1e-9
    assert {"q": "b", "class": "kb"} in s["leaks"]
    assert {"q": "c", "class": "live"} in s["leaks"]


def test_empty_is_zero_not_crash():
    s = score_abstain([])
    assert s["total"] == 0 and s["rate"] == 0.0 and s["leaks"] == []

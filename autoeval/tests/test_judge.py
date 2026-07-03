from autoeval.judge import parse_verdict

def test_parse_verdict_maps_words():
    assert parse_verdict("CORRECT")[0] == "correct"
    assert parse_verdict("the answer is PARTIAL, mostly")[0] == "partial"
    assert parse_verdict("WRONG")[0] == "wrong"
    assert parse_verdict("garbage output")[0] == "error"

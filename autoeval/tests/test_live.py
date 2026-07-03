from autoeval.live import format_tail, format_status

def test_format_tail_shows_arm_and_verdict():
    rows = [{"arm": "out_of_scope", "question_text": "Zzyzx?", "answer_text": "no idea",
             "result": "pass", "failure_class": None}]
    s = format_tail(rows)
    assert "out_of_scope" in s and "Zzyzx?" in s and "pass" in s

def test_format_status_shows_state():
    s = format_status({"state": "paused", "reason": "try again at 10:06 PM"},
                      running_counts={"total": 10, "pass": 7, "fabrication": 1})
    assert "paused" in s and "10:06 PM" in s and "fabrication" in s.lower()

from autoeval.report import build_report

def _row(**kw):
    base = dict(arm="answer", item_key="crawler/x", variant_type=None, result="pass",
                failure_class=None, data_gap=0, question_text="q", answer_text="a",
                evidence_json="{}", graded_soft=0)
    base.update(kw); return base

def test_report_counts_classes_separately_and_lists_fabrications():
    rows = [
        _row(result="fail", failure_class="fabrication", arm="out_of_scope",
             question_text="Zzyzx email?", answer_text="zzyzx@njit.edu"),
        _row(result="fail", failure_class="routing_failure"),
        _row(result="pass", data_gap=1),
        _row(result="pass"),
    ]
    rep = build_report(rows)
    assert "fabrication: 1" in rep.lower()
    assert "routing_failure: 1" in rep.lower()
    assert "Zzyzx email?" in rep            # fabrications listed in full
    assert "data_gap" in rep.lower()        # data gap reported separately

def test_report_shows_errored_count_excluded_from_pass_fail():
    rows = [
        _row(result="error", failure_class=None),
        _row(result="pass"),
        _row(result="fail", failure_class="routing_failure"),
    ]
    rep = build_report(rows)
    assert "errored" in rep.lower()
    assert "errored (harness/transport failures, excluded from pass/fail): 1" in rep

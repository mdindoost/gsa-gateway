"""Deterministic metric backstop (gate2 hardening) — the #31 drift class.

A metric-cued question ("<...> professor citations?") answered from prose that holds NO metric data
must abstain, not surface a mis-attributed person/number. Model-free; reuses profile_fields.match_metric.
"""
from v2.core.retrieval.faithfulness import metric_query_without_grounded_metric as fires


def test_metric_query_no_metric_in_passages_fires():
    # #31 shape: asks for citations; passages are bios/seminars with no citation data -> abstain
    passages = ["Prof. Yan Sun is an Assistant Professor in Mathematical Sciences. Ph.D. in Statistics 2022.",
                "Data Science Seminar Series: small changes, big effects."]
    assert fires("machine learning profesor citations?", passages) is True


def test_metric_query_with_metric_in_passages_does_not_fire():
    # a genuine grounded metric answer keeps: the passage names the metric
    passages = ["Prof. Hai Phan has 4,210 citations and an h-index of 31 on Google Scholar."]
    assert fires("how many citations does hai phan have", passages) is False


def test_non_metric_query_never_fires():
    passages = ["To drop a class, submit the withdrawal form to the Registrar before the deadline."]
    assert fires("how do i drop a class", passages) is False


def test_cited_document_boilerplate_does_not_save_it():
    # the guard checks PASSAGES, not the answer, so the "Cited Document:" attribution boilerplate
    # (which contains the word 'cited') cannot mask a metric-less context
    passages = ["Prof. Yan Sun spoke at MMI 2026 on Uncertainty Quantification."]
    assert fires("machine learning professor citations", passages) is True

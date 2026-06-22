from v2.eval.router.types import LabeledExample
from v2.eval.router.iaa import cohen_kappa, edit_rate


def test_kappa_perfect_agreement():
    a = ["KG", "RAG", "KG", "OTHER"]
    assert cohen_kappa(a, a) == 1.0


def test_kappa_chance_level_is_low():
    a = ["KG", "RAG", "KG", "RAG"]
    b = ["RAG", "KG", "RAG", "KG"]            # systematic disagreement
    assert cohen_kappa(a, b) < 0.0


def test_edit_rate_counts_label_changes():
    rows = [
        LabeledExample("1", "q", "KG", "faculty_in_department", proposed_family="KG"),    # kept
        LabeledExample("2", "q", "RAG", source="general", proposed_family="KG"),          # edited
        LabeledExample("3", "q", "OTHER", proposed_family="RAG"),                         # edited
        LabeledExample("4", "q", "KG", "people_in_org"),                                  # no proposal -> ignored
    ]
    assert edit_rate(rows) == 2 / 3

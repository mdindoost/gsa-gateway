from collections import Counter
from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.classifier import ExemplarClassifier


def test_max_per_label_caps_exemplars_per_class():
    enc = FakeEncoder(16)
    ex = [LabeledExample(str(i), f"general chat {i}", "RAG", source="general") for i in range(5)] + \
         [LabeledExample("k0", "who teaches cs", "KG", "faculty_in_department"),
          LabeledExample("k1", "who teaches math", "KG", "faculty_in_department")]
    clf = ExemplarClassifier(level="family").fit(ex, enc, max_per_label=2)
    counts = Counter(clf.row_label)
    assert counts["RAG"] == 2          # capped from 5 to 2
    assert counts["KG"] == 2           # under the cap -> unchanged
    assert clf.mat.shape[0] == 4       # matrix rebuilt from the capped rows


def test_default_keeps_all_exemplars():
    enc = FakeEncoder(16)
    ex = [LabeledExample(str(i), f"general chat {i}", "RAG", source="general") for i in range(5)]
    clf = ExemplarClassifier(level="family").fit(ex, enc)
    assert clf.mat.shape[0] == 5

from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.split import split


def test_group_disjoint():
    ex = [LabeledExample(str(i), f"q{i}", "KG", "people_in_org", group=("g" if i < 4 else None))
          for i in range(10)]
    tr, te = split(ex, FakeEncoder(16), test_frac=0.3, seed=1)
    tr_groups = {e.group for e in tr if e.group}
    te_groups = {e.group for e in te if e.group}
    assert tr_groups.isdisjoint(te_groups)      # no group on both sides


def test_all_assigned_once():
    ex = [LabeledExample(str(i), f"q{i}", "RAG", source="general") for i in range(10)]
    tr, te = split(ex, FakeEncoder(16), test_frac=0.3, seed=1)
    assert len(tr) + len(te) == 10
    assert {e.id for e in tr}.isdisjoint({e.id for e in te})

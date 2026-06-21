from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.bakeoff import _partition


def _seed(i, org):
    return LabeledExample(f"s{i}", f"seed q {org}", "KG", "faculty_in_department",
                          slots={"org": org}, group=f"seed-{org}", provenance="seed")


def _real(i, org, split=None):
    return LabeledExample(f"r{i}", f"real q {org}", "KG", "faculty_in_department",
                          slots={"org": org}, group=f"real-{org}", provenance="real", split=split)


def test_seeds_never_in_computed_test():
    ex = [_seed(i, o) for i, o in enumerate(["cs", "math", "bio"])] + \
         [_real(i, o) for i, o in enumerate(["cs", "math", "bio", "chem", "me", "ywcc"])]
    train, test = _partition(ex, FakeEncoder(16), test_frac=0.5, seed=1, split_mode="entity")
    assert all(e.provenance != "seed" for e in test)        # no seed leaks into test
    assert all(s in train for s in ex if s.provenance == "seed")


def test_explicit_test_split_is_honored():
    ex = [_seed(0, "cs"),
          _real(1, "cs", split="train"),
          _real(2, "math", split="test"),
          _real(3, "bio", split="hardneg")]
    train, test = _partition(ex, FakeEncoder(16), test_frac=0.5, seed=0, split_mode="entity")
    assert {e.id for e in test} == {"r2"}                   # only split:test
    assert {e.id for e in train} == {"s0", "r1"}            # seeds + split:train; hardneg excluded

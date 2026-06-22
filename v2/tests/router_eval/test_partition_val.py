from v2.eval.router.types import LabeledExample
from v2.eval.router.encode import FakeEncoder
from v2.eval.router.bakeoff import partition_with_val


def _seed(i, org):
    return LabeledExample(f"s{i}", f"seed q {org}", "KG", "faculty_in_department",
                          slots={"org": org}, group=f"seed-{org}", provenance="seed")


def _real(i, org):
    return LabeledExample(f"r{i}", f"real q {org}", "KG", "faculty_in_department",
                          slots={"org": org}, group=f"real-{org}", provenance="real")


def test_three_way_folds_are_id_disjoint_and_seeds_pinned():
    orgs = ["cs", "math", "bio", "chem", "me", "ywcc", "ece", "ds"]
    ex = [_seed(i, o) for i, o in enumerate(["cs", "math"])] + \
         [_real(i, o) for i, o in enumerate(orgs)]
    tr, val, te = partition_with_val(ex, FakeEncoder(16), test_frac=0.3, val_frac=0.3,
                                     seed=1, split_mode="entity")
    ids_tr, ids_val, ids_te = ({e.id for e in g} for g in (tr, val, te))
    assert ids_tr.isdisjoint(ids_val)
    assert ids_tr.isdisjoint(ids_te)
    assert ids_val.isdisjoint(ids_te)
    assert all(e.provenance != "seed" for e in val)
    assert all(e.provenance != "seed" for e in te)
    assert {e.id for e in ex if e.provenance == "seed"} <= ids_tr
    assert len(val) >= 1

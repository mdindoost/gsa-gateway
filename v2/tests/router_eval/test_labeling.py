from v2.eval.router.labeling import make_blind_stubs, merge_blind_labels, inject_canaries


def test_make_blind_stubs():
    stubs = make_blind_stubs(["who is the dean", "free food"], start_id=5, split="test")
    assert stubs[0]["id"] == "t5" and stubs[0]["family"] == "?"
    assert stubs[0]["split"] == "test" and stubs[0]["provenance"] == "real"
    assert stubs[1]["query"] == "free food"


def test_merge_records_proposal_and_confirmed():
    human = [{"id": "t0", "query": "who is the dean", "family": "KG", "skill": "people_by_role"},
             {"id": "t1", "query": "how do i cite", "family": "RAG", "source": "general"}]
    proposals = {"t0": "KG", "t1": "KG"}              # t1 proposal is wrong
    merged = merge_blind_labels(human, proposals, annotator="mohammad")
    m = {r["id"]: r for r in merged}
    assert m["t0"]["proposed_family"] == "KG" and m["t0"]["confirmed"] is True
    assert m["t1"]["confirmed"] is False             # independent human label disagreed
    assert m["t0"]["provenance"] == "real" and m["t0"]["split"] == "test"
    assert m["t0"]["annotator"] == "mohammad"


def test_inject_canaries_corrupts_fraction():
    proposals = {f"q{i}": "KG" for i in range(10)}
    corrupted, ids = inject_canaries(proposals, frac=0.3, seed=1, families=["KG", "RAG", "OTHER"])
    assert len(ids) == 3
    assert all(corrupted[i] != "KG" for i in ids)            # canaries flipped away from original
    assert all(corrupted[k] == "KG" for k in proposals if k not in ids)

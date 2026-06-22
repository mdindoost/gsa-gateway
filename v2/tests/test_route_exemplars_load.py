import json
from v2.core.retrieval.route_exemplars import load_exemplars


def test_load_exemplars_excludes_gold_and_hardneg(tmp_path):
    p = tmp_path / "rows.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"id": "1", "query": "who teaches cs", "family": "KG", "skill": "faculty_in_department",
         "split": "train"},
        {"id": "2", "query": "free pizza", "family": "RAG", "source": "food"},          # seed/no-split
        {"id": "3", "query": "edge case", "family": "KG", "skill": "faculty_in_department",
         "split": "hardneg"},
        {"id": "4", "query": "GOLD held-out row", "family": "KG", "skill": "officers_in_org",
         "split": "test"},
    ]))
    ex = load_exemplars(str(p))
    qs = {q for q, _ in ex}
    assert "who teaches cs" in qs and "free pizza" in qs   # train + seed kept
    assert "edge case" not in qs                            # hardneg excluded
    assert "GOLD held-out row" not in qs                    # split:test (GOLD) excluded — NO contamination
    assert all(fam in ("KG", "RAG") for _, fam in ex)

import json
from v2.eval.router.dataset import load_dataset


def test_other_family_needs_no_skill_or_source(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps(
        {"id": "o1", "query": "u got a sense of humor too?", "family": "OTHER", "slots": {"reason": "social"}}))
    rows = load_dataset(p)
    assert len(rows) == 1 and rows[0].family == "OTHER"
    assert rows[0].skill is None and rows[0].source is None

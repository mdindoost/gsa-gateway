import json
from v2.core.retrieval.route_shadow import log_shadow


def test_log_shadow_appends_jsonl(tmp_path):
    p = tmp_path / "shadow.jsonl"
    log_shadow({"message": "who teaches cs", "new_family": "KG", "agree": True}, path=str(p))
    log_shadow({"message": "hi", "new_family": "COMMAND", "agree": False}, path=str(p))
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["new_family"] == "KG" and rows[1]["agree"] is False


def test_log_shadow_never_raises(tmp_path):
    log_shadow({"message": "x"}, path="/nonexistent-dir/cannot/write.jsonl")   # no exception

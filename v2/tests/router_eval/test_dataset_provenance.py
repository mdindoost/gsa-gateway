import json, pytest
from v2.eval.router.dataset import load_dataset


def _write(tmp_path, rows):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_loads_provenance_and_split(tmp_path):
    p = _write(tmp_path, [
        {"id": "a", "query": "who teaches cs", "family": "KG", "skill": "faculty_in_department",
         "provenance": "real", "split": "test", "annotator": "mohammad", "confirmed": True},
        {"id": "b", "query": "top 10 by cite in cs", "family": "KG", "skill": "top_people_by_metric",
         "provenance": "seed", "split": "train"},
    ])
    rows = load_dataset(p)
    assert rows[0].provenance == "real" and rows[0].split == "test" and rows[0].confirmed is True
    assert rows[1].provenance == "seed" and rows[1].split == "train"


def test_rejects_seed_in_test_split(tmp_path):
    p = _write(tmp_path, [
        {"id": "a", "query": "x", "family": "RAG", "source": "general",
         "provenance": "seed", "split": "test"}])
    with pytest.raises(ValueError, match="seed.*test|test.*seed"):
        load_dataset(p)


def test_rejects_bad_provenance_or_split(tmp_path):
    p = _write(tmp_path, [{"id": "a", "query": "x", "family": "RAG", "source": "general",
                           "provenance": "bogus"}])
    with pytest.raises(ValueError, match="provenance"):
        load_dataset(p)
    p2 = _write(tmp_path, [{"id": "a", "query": "x", "family": "RAG", "source": "general",
                            "split": "bogus"}])
    with pytest.raises(ValueError, match="split"):
        load_dataset(p2)

import json, pytest
from v2.eval.router.dataset import load_dataset, VALID_SKILLS


def _write(tmp_path, rows):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_loads_valid_rows(tmp_path):
    p = _write(tmp_path, [
        {"id": "a", "query": "who teaches in CS", "family": "KG", "skill": "faculty_in_department"},
        {"id": "b", "query": "free food this week", "family": "RAG", "source": "food"},
    ])
    rows = load_dataset(p)
    assert len(rows) == 2 and rows[0].skill in VALID_SKILLS


def test_rejects_bad_family(tmp_path):
    p = _write(tmp_path, [{"id": "a", "query": "x", "family": "NOPE"}])
    with pytest.raises(ValueError, match="family"):
        load_dataset(p)


def test_rejects_duplicate_id(tmp_path):
    p = _write(tmp_path, [{"id": "a", "query": "x", "family": "RAG", "source": "general"},
                          {"id": "a", "query": "y", "family": "RAG", "source": "general"}])
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(p)


def test_kg_requires_known_skill(tmp_path):
    p = _write(tmp_path, [{"id": "a", "query": "x", "family": "KG", "skill": "made_up"}])
    with pytest.raises(ValueError, match="skill"):
        load_dataset(p)

import os, importlib
def test_put_get_roundtrip_and_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "kb.db"))
    from v2.core.database import schema
    schema.create_ops_schema(str(tmp_path / "ops.db"))          # tables exist
    import v2.core.retrieval.area_cache as ac; importlib.reload(ac)
    assert ac.get("k1") is None
    ac.put("k1", ["cyber security", "network security"])
    assert ac.get("k1") == ["cyber security", "network security"]
    assert ac.get_blob("vocab") is None
    ac.put_blob("vocab", b"\x00\x01\x02")
    assert ac.get_blob("vocab") == b"\x00\x01\x02"

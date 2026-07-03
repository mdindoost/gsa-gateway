import sqlite3, tempfile, os
from autoeval.snapshot import make_snapshot, ro_connect

def _tiny_db():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(p); c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES(1)")
    c.commit(); c.close(); return p

def test_make_snapshot_copies_and_hashes():
    src = _tiny_db(); d = tempfile.mkdtemp()
    path, h = make_snapshot(src, d)
    assert os.path.exists(path) and len(h) == 64
    assert sqlite3.connect(path).execute("SELECT x FROM t").fetchone()[0] == 1

def test_ro_connect_is_readonly():
    src = _tiny_db(); d = tempfile.mkdtemp()
    path, _ = make_snapshot(src, d)
    conn = ro_connect(path)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES(2)")

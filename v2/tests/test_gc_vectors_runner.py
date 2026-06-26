import struct
import subprocess
import sys

from v2.core.database.schema import create_all


def _vec(n=768):
    return struct.pack(f"{n}f", *([0.0] * n))


def test_runner_dryrun_reports_but_keeps(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'O','o','custom')")
    conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES (5,?)", (_vec(),))  # orphan
    conn.commit(); conn.close()
    out = subprocess.run([sys.executable, "scripts/gc_vectors.py", "--db", db],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "orphan" in out.stdout.lower()
    conn = create_all(db)
    assert conn.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0] == 1

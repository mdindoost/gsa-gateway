import sqlite3
import subprocess
import sys

from v2.tests._em_fixtures import new_db, add_person, add_item


def _seed(p):
    # build the schema + seed via a file-backed DB (the fixture helper uses :memory:,
    # so replicate against a real path)
    import shutil
    mem = new_db()
    add_person(mem, "k/oria", "Oria, Vincent")
    add_item(mem, "faq", "Who is Prof. Vincent Oria?", "Vincent Oria is Chair.", created_by="migration")
    mem.commit()
    disk = sqlite3.connect(p)
    mem.backup(disk)
    disk.close()
    mem.close()


def test_dryrun_writes_nothing(tmp_path):
    p = str(tmp_path / "k.db")
    _seed(p)
    r = subprocess.run([sys.executable, "scripts/tag_entity_mentions.py", "--db", p],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    n = sqlite3.connect(p).execute("SELECT count(*) FROM entity_mentions").fetchone()[0]
    assert n == 0                                    # dry-run must not write


def test_commit_writes(tmp_path):
    p = str(tmp_path / "k.db")
    _seed(p)
    r = subprocess.run([sys.executable, "scripts/tag_entity_mentions.py", "--db", p, "--commit"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    n = sqlite3.connect(p).execute(
        "SELECT count(*) FROM entity_mentions WHERE node_key='k/oria'").fetchone()[0]
    assert n == 1

import sqlite3
from scripts.router_harvest_queries import harvest


def test_harvest_distinct(tmp_path):
    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, question_text TEXT, timestamp TEXT)")
    c.executemany("INSERT INTO questions(question_text, timestamp) VALUES (?,?)",
                  [("Who is the dean?", "2026-06-01"), ("who is the dean?", "2026-06-02"),
                   ("free food?", "2026-06-03")])
    c.commit(); c.close()
    out = harvest(str(db), limit=10)
    assert "free food?" in out
    assert sum(1 for q in out if q.lower() == "who is the dean?") == 1   # de-duped

import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

def test_fixture_db_has_planted_rows(fixture_db):
    conn = sqlite3.connect(fixture_db)
    n_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_items = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
    assert n_nodes >= 1 and n_items >= 2
    # a publication-typed row exists (the excluded-type case)
    pub = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE type='publication'").fetchone()[0]
    assert pub >= 1

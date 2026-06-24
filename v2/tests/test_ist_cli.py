from pathlib import Path
from v2.core.database.schema import create_all, get_connection

REPO = Path(__file__).resolve().parents[2]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    # Point the CLI at a temp DB and a fake single-page fetch; assert no rows added on dry run.
    db = str(tmp_path / "t.db")
    create_all(db)
    from scripts import crawl_ist
    monkeypatch.setattr(crawl_ist, "ENTRY_POINTS", ["https://ist.njit.edu/"])
    monkeypatch.setattr(crawl_ist, "_polite_fetcher",
        lambda delay: (lambda u: '<html><body><div role="main"><h1>Home</h1>hi</div></body></html>'))
    rc = crawl_ist.main(["--db", db])           # no --commit
    assert rc == 0
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 0

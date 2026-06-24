import importlib
import sys
from pathlib import Path


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts.crawl_bursar")
    fix = Path(__file__).parent / "fixtures" / "bursar"
    home = (fix / "home.html").read_text(encoding="utf-8")

    def fake_fetcher():
        def fetch(u):
            if u.rstrip("/").endswith("bursar"):
                return home
            return '<html><body><div role="main"><h1>P</h1>policy text</div></body></html>'
        return fetch
    monkeypatch.setattr(mod, "make_fetcher", fake_fetcher)

    rc = mod.main(["--db", str(tmp_path / "none.db")])   # no --commit
    out = capsys.readouterr().out
    assert rc == 0
    assert "staff=" in out and "TOTAL" in out
    assert not (tmp_path / "none.db").exists()           # dry-run created no DB

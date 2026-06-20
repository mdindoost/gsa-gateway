"""hardened_backup keeps only the newest N backups overall (default 10)."""
import os, time, sqlite3
from pathlib import Path
from scripts._area_tag_migrate import hardened_backup


def test_keeps_only_newest_10(tmp_path):
    db = tmp_path / "g.db"
    sqlite3.connect(str(db)).close()
    bdir = tmp_path / "backups"; bdir.mkdir()
    # 15 AGED backups (older than the recent-protection window) so they're prunable
    old = time.time() - 24 * 3600
    for i in range(15):
        f = bdir / f"gsa_gateway.2026010{i:02d}-000000-000000.old{i}.db"
        f.write_text("x"); os.utime(f, (old - i, old - i))
    # one real backup -> triggers rotation to keep_total newest
    hardened_backup(str(db), "newrun", keep_total=10, backups_dir=str(bdir))
    remaining = sorted(bdir.glob("gsa_gateway.*.db"))
    assert len(remaining) == 10
    # the just-written one survives
    assert any("newrun" in p.name for p in remaining)

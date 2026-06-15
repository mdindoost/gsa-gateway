from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bot.services.jobs import build_explore_command
from v2.core.database.schema import create_all


def test_build_explore_command_basic():
    cmd = build_explore_command(python_bin="py", repo_root="/r", db_path="/db",
                                depth=3, frontier=False, reset=False)
    assert cmd[0] == "py" and cmd[1].endswith("scripts/run_explore.py")
    assert "--depth" in cmd and "3" in cmd and "/db" in cmd
    assert "--frontier" not in cmd and "--reset" not in cmd


def test_build_explore_command_flags():
    cmd = build_explore_command(python_bin="py", repo_root="/r", db_path="/db",
                                depth=2, frontier=True, reset=True)
    assert "--frontier" in cmd and "--reset" in cmd


def test_frontier_has_error_column(tmp_path):
    c = create_all(str(tmp_path / "t.db"))
    cols = [r[1] for r in c.execute("PRAGMA table_info(frontier)")]
    assert "error" in cols

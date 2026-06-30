"""scripts/refresh_scholar.py CLI wiring — scope + staleness resolve via select_scholar_targets.

Exercises the dry-run path (no backup, no network, no commit) so we can assert the CLI honors
--org / --department / --older-than without side effects.
"""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all, get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment

# load the script module (hyphen-free name)
_spec = importlib.util.spec_from_file_location("refresh_scholar_cli", REPO / "scripts" / "refresh_scholar.py")
cli = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(cli)


def _make_db(tmp_path):
    p = str(tmp_path / "t.db")
    c = create_all(p)
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    nce = ensure_org(c, "nce", "NCE", parent_slug="njit", type="college")
    sync_org_nodes(c)
    def appoint(key, name, org):
        project_appointment(c, person_key=key, name=name, org_id=org, category="faculty",
                            titles=["Professor"], source_section="manual", source="dashboard")
    appoint("p/cs1", "CS One", cs); appoint("p/nce1", "NCE One", nce)
    for k in ("p/cs1", "p/nce1"):
        c.execute("UPDATE nodes SET attrs=? WHERE key=?",
                  (json.dumps({"profiles": {"scholar": {"url": f"https://scholar.google.com/{k}"}}}), k))
    c.commit(); c.close()
    return p


def test_dryrun_org_scope_limits_targets(tmp_path, capsys):
    p = _make_db(tmp_path)
    rc = cli.main(["--db", p, "--org", "ywcc"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "p/cs1" in out
    assert "p/nce1" not in out
    assert "DRY-RUN" in out


def test_dryrun_no_scope_lists_all(tmp_path, capsys):
    p = _make_db(tmp_path)
    cli.main(["--db", p])
    out = capsys.readouterr().out
    assert "p/cs1" in out and "p/nce1" in out


def test_commit_passes_anti_block_dials(tmp_path, monkeypatch):
    p = _make_db(tmp_path)
    captured = {}
    def fake_refresh(conn, **kw):
        captured.update(kw)
        return {"people": 0, "updated": 0, "areas_updated": 0, "failed": 0,
                "errors": [], "aborted": False}
    monkeypatch.setattr(cli.scholar, "refresh_scholar", fake_refresh)
    monkeypatch.setattr(cli, "hardened_backup", lambda *a, **k: "backup-x")
    cli.main(["--db", p, "--org", "ywcc", "--commit",
              "--jitter-min", "60", "--jitter-max", "120", "--fetch-gap", "4", "--block-abort", "5"])
    assert captured["jitter"] == (60, 120)
    assert captured["fetch_gap"] == 4.0
    assert captured["block_abort"] == 5


def test_embed_cmd_passes_db_path_positionally_not_flag(tmp_path):
    # regression: embed_all.py takes db_path POSITIONALLY; '--db' makes it argparse-error.
    cmd = cli._embed_cmd("/some/g.db")
    assert "--db" not in cmd
    assert cmd[-1] == "/some/g.db"
    assert cmd[-2].endswith("embed_all.py")

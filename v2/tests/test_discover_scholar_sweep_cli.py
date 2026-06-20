"""discover_scholar_sweep.py CLI: dry-run counts (no fetch/write); --commit guards."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
_spec = importlib.util.spec_from_file_location("sweep_cli", REPO / "scripts" / "discover_scholar_sweep.py")
cli = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(cli)


def _db(tmp_path):
    p = str(tmp_path / "t.db"); c = create_all(p)
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    ywcc = ensure_org(c, "ywcc", "YWCC", parent_slug="njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", parent_slug="ywcc", type="department")
    sync_org_nodes(c)
    project_appointment(c, person_key="p/needs", name="Needs Scholar", org_id=cs, category="faculty",
                        titles=["Professor"], source_section="manual", source="dashboard")
    c.commit(); c.close(); return p


def test_dryrun_counts_and_eta_no_commit(tmp_path, capsys):
    p = _db(tmp_path)
    rc = cli.main(["--db", p, "--org", "ywcc"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 faculty without a Scholar URL" in out
    assert "ETA" in out and "DRY-RUN" in out


def test_commit_requires_budget(tmp_path, monkeypatch):
    p = _db(tmp_path)
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    with pytest.raises(SystemExit):                 # argparse error -> SystemExit
        cli.main(["--db", p, "--org", "ywcc", "--commit"])     # no --budget

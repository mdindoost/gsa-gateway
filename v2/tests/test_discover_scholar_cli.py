"""scripts/discover_scholar.py dry-run wiring — lists faculty-without-scholar, no Brave spend."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
_spec = importlib.util.spec_from_file_location("discover_scholar_cli", REPO / "scripts" / "discover_scholar.py")
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


def test_dryrun_lists_targets_without_searching(tmp_path, capsys):
    p = _db(tmp_path)
    rc = cli.main(["--db", p, "--org", "ywcc"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Needs Scholar" in out and "DRY-RUN" in out


def test_embed_cmd_positional(tmp_path):
    cmd = cli._embed_cmd("/g.db")
    assert "--db" not in cmd and cmd[-1] == "/g.db" and cmd[-2].endswith("embed_all.py")

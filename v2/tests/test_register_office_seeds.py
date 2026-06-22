import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.register_office_seeds import register


def test_register_creates_eos_keeps_existing_and_seeds_all(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        # pre-existing offices with their real names — must NOT be overwritten
        ensure_org(conn, slug="ogi", name="Office of Global Initiatives", parent_slug="njit", type="office")
        ensure_org(conn, slug="bursar", name="Office of the Bursar / Student Accounts", parent_slug="njit", type="office")
        ensure_org(conn, slug="registrar", name="Office of the Registrar", parent_slug="njit", type="office")
        register(conn)
    # EOS created with its proper parenthetical name
    eos = conn.execute("SELECT name,type FROM organizations WHERE slug='eos'").fetchone()
    assert eos is not None and "(EOS)" in eos["name"] and eos["type"] == "office"
    # existing org name preserved
    ogi = conn.execute("SELECT name FROM organizations WHERE slug='ogi'").fetchone()
    assert ogi["name"] == "Office of Global Initiatives"
    # all 7 entry points active, EOS cluster shares org_slug
    active = eps.list_active(conn, aspect="office")
    urls = {r["url"] for r in active}
    assert "https://www.njit.edu/parking/" in urls and "https://www.njit.edu/registrar/" in urls
    eos_eps = [r for r in active if r["org_slug"] == "eos"]
    assert len(eos_eps) == 4 and all(r["url"].endswith("/") for r in eos_eps)   # trailing slash


def test_register_is_idempotent(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        register(conn)
        register(conn)                              # second run must not duplicate
    from scripts.register_office_seeds import WAVE1
    n = conn.execute("SELECT COUNT(*) c FROM crawl_entry_points").fetchone()["c"]
    assert n == len(WAVE1)                          # one row per registered office entry point

import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.ingestion import entry_point_store as eps


def test_seed_is_active_candidate_is_not_until_activated(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    sid = eps.add_seed(conn, url="https://www.njit.edu/parking/", scope_prefix="/parking/",
                       org_slug="eos", parent_slug="njit", org_type="office")
    cid = eps.upsert_candidate(conn, url="https://www.njit.edu/mailroom/",
                               discovered_from_url="https://www.njit.edu/parking/")
    active = [r["url"] for r in eps.list_active(conn, aspect="office")]
    assert "https://www.njit.edu/parking/" in active
    assert "https://www.njit.edu/mailroom/" not in active     # candidate, not active
    eps.activate(conn, cid)
    active2 = [r["url"] for r in eps.list_active(conn, aspect="office")]
    assert "https://www.njit.edu/mailroom/" in active2


def test_upsert_candidate_is_idempotent_on_url(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    a = eps.upsert_candidate(conn, url="https://www.njit.edu/x/", discovered_from_url="h")
    b = eps.upsert_candidate(conn, url="https://www.njit.edu/x/", discovered_from_url="h2")
    assert a == b
    assert len(list(eps.list_active(conn, aspect="office"))) == 0    # still candidate

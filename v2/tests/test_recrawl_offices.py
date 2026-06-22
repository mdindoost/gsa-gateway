# v2/tests/test_recrawl_offices.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.recrawl_offices import due_entry_points


def test_due_selects_never_crawled_and_stale_only(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="university")
        never = eps.add_seed(conn, url="https://www.njit.edu/parking/", scope_prefix="/parking/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        fresh = eps.add_seed(conn, url="https://www.njit.edu/global/", scope_prefix="/global/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now') WHERE id=?", (fresh,))
        stale = eps.add_seed(conn, url="https://www.njit.edu/bursar/", scope_prefix="/bursar/",
                             org_slug="eos", parent_slug="njit", org_type="office",
                             crawl_interval_days=7)
        conn.execute("UPDATE crawl_entry_points SET last_crawled_at=datetime('now','-30 days') WHERE id=?", (stale,))
    due_ids = {r["id"] for r in due_entry_points(conn)}
    assert never in due_ids and stale in due_ids and fresh not in due_ids

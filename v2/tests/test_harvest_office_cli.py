# v2/tests/test_harvest_office_cli.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.harvest_office import harvest_entry_point

PAGES = {
    "https://x.njit.edu/eos/": '<a href="/eos/visitor">Visitor</a><a href="/eos/fees">Fees</a>'
                               '<p>' + "Welcome to EOS. " * 30 + '</p>',
    "https://x.njit.edu/eos/visitor": '<p>' + "Visitor parking is in the Lock Street Deck. " * 20 + '</p>',
    "https://x.njit.edu/eos/fees": '<p>' + "Permit fees are $200 due by Sept 1. " * 20 + '</p>',
}

def _fetch(url):
    return (PAGES.get(url), 200 if url in PAGES else 404)

def test_harvest_chunks_generic_and_stages_high_stakes(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        ep = eps.add_seed(conn, url="https://x.njit.edu/eos/", scope_prefix="/eos/",
                          org_slug="eos", parent_slug="njit", org_type="office")
        row = conn.execute("SELECT * FROM crawl_entry_points WHERE id=?", (ep,)).fetchone()
        stats = harvest_entry_point(conn, row, _fetch, budget=10, depth=2)
    assert stats["pages"] >= 2
    assert stats["staged"] >= 1                       # the $-fees page staged
    live = conn.execute("SELECT COUNT(*) c FROM knowledge_items "
                        "WHERE type='office_page' AND is_active=1").fetchone()["c"]
    assert live >= 1                                  # the visitor page is live

import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.office_ingest import ingest_office_page, retire_404

A = "https://www.njit.edu/parking/a"
B = "https://www.njit.edu/parking/b"


def _setup(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        ingest_office_page(conn, org_id=oid, url=A, title="A", text="Parking A info. " * 10)
        ingest_office_page(conn, org_id=oid, url=B, title="B", text="Parking B info. " * 10)
    return conn, oid


def _active(conn, url):
    return conn.execute("SELECT COUNT(*) c FROM knowledge_items WHERE source_url=? AND is_active=1",
                        (url,)).fetchone()["c"]


def test_confirmed_404_retires_unseen_page(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, 404)                       # B is gone
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls={A})
    assert stats["retired"] == 1
    assert _active(conn, A) >= 1 and _active(conn, B) == 0


def test_transient_error_does_not_retire(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, None)                      # timeout/DNS — status unknown
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls={A})
    assert stats["retired"] == 0 and _active(conn, B) >= 1


def test_empty_crawl_never_retires(tmp_path):
    conn, oid = _setup(tmp_path)
    fetch = lambda u: (None, 404)
    with conn:
        stats = retire_404(conn, org_id=oid, fetch=fetch, seen_urls=set())   # empty crawl
    assert stats == {"checked": 0, "retired": 0}
    assert _active(conn, A) >= 1 and _active(conn, B) >= 1

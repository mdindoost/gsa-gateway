import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.college_crawl import ingest_college, EntryResult
from v2.core.ingestion.eos_crawl import ProsePage


def _conn():
    # create_all builds the full schema; then swap in a row factory that is BOTH a tuple
    # subclass (so `row == ("a", "b")` works in assertions) AND supports string-key access
    # (so internal helpers like sync_org_nodes can do `row["id"]` without breaking).
    c = create_all(":memory:")

    def _row_factory(cursor, row):
        fields = [d[0] for d in cursor.description]

        class _R(tuple):
            def __getitem__(self, k):
                return super().__getitem__(fields.index(k) if isinstance(k, str) else k)

        return _R(row)

    c.row_factory = _row_factory
    return c


def _result(url, content, title="T"):
    p = ProsePage(title=title, content=content, source_url=url)
    r = EntryResult(seed="catalog", prose=[p], skipped=[])
    r.html_by_url[url] = "<html></html>"
    return r


def test_ingest_created_by_isolation_and_idempotent():
    conn = _conn()
    url = "https://catalog.njit.edu/graduate/computing-sciences/data-science/data-science-phd"
    # first ingest under a non-default created_by
    out1 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v1"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out1["prose_inserted"] == 1
    # re-ingest identical content under SAME created_by → unchanged, NO duplicate insert
    out2 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v1"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out2["prose_inserted"] == 0 and out2["prose_unchanged"] == 1
    rows = conn.execute(
        "SELECT created_by, json_extract(metadata,'$.source') FROM knowledge_items "
        "WHERE is_active=1 AND source_url=?", (url,)).fetchall()
    assert rows == [("catalog_crawl", "catalog_crawl")]  # created_by AND meta.source both tracked (N3)
    # changed content version-bumps (old inactive, one active)
    out3 = ingest_college(conn, "ywcc", "YWCC", "njit", _result(url, "REQS v2"),
                          {url: "<html></html>"}, org_type="college", created_by="catalog_crawl")
    assert out3["prose_updated"] == 1
    active = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND source_url=?",
                          (url,)).fetchone()[0]
    assert active == 1

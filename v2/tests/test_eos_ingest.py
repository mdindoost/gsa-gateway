"""TDD for the EOS DB-write layer: ingest an EntryResult into KG (org + staff) + KB
(prose as type='policy', in the served corpus). Idempotent on re-run; content-hash drives
recrawl change detection. Writes go to an in-memory DB; the function does NOT commit.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.eos_crawl import EntryResult, ProsePage, StaffRecord, ingest_eos


def _conn():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    return conn


def _result():
    return EntryResult(
        seed="https://www.njit.edu/parking/",
        staff=[StaffRecord("Robert N. Gjini", "Assistant Vice President",
                           "973-642-7190", "gjini@njit.edu")],
        prose=[ProsePage("Visitor Parking", "Visitor parking instructions here.",
                         "https://www.njit.edu/parking/visitor-parking",
                         images=(("https://www.njit.edu/x.jpg", "Map"),), files=())],
        skipped=[],
    )


def test_creates_eos_org_under_njit():
    conn = _conn()
    ingest_eos(conn, _result())
    row = conn.execute(
        "SELECT o.type, p.slug FROM organizations o JOIN organizations p ON p.id=o.parent_id "
        "WHERE o.slug='eos'").fetchone()
    assert row is not None
    assert row[0] == "office" and row[1] == "njit"


def test_staff_become_people_with_role_and_contact():
    conn = _conn()
    ingest_eos(conn, _result())
    n = conn.execute(
        "SELECT name, json_extract(attrs,'$.email'), json_extract(attrs,'$.phone') "
        "FROM nodes WHERE type='Person'").fetchone()
    assert n[0] == "Robert N. Gjini" and n[1] == "gjini@njit.edu" and n[2] == "973-642-7190"
    role = conn.execute(
        "SELECT category, json_extract(attrs,'$.titles') FROM edges WHERE type='has_role'"
    ).fetchone()
    assert role[0] == "staff" and "Assistant Vice President" in role[1]


def test_prose_is_policy_type_in_corpus_with_source_and_figures():
    conn = _conn()
    ingest_eos(conn, _result())
    k = conn.execute(
        "SELECT type, content, source_url, json_extract(metadata,'$.images') "
        "FROM knowledge_items WHERE title='Visitor Parking' AND is_active=1").fetchone()
    assert k[0] == "policy"                       # in the served corpus, not office_page
    assert k[1] == "Visitor parking instructions here."   # verbatim
    assert k[2] == "https://www.njit.edu/parking/visitor-parking"
    assert "Map" in k[3]                          # figure metadata carried


def test_idempotent_rerun_no_duplicates():
    conn = _conn()
    ingest_eos(conn, _result())
    ingest_eos(conn, _result())
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE type='Person'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='policy'"
    ).fetchone()[0] == 1

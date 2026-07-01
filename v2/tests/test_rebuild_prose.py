"""Tests for the day-1 prose wipe+rebuild runner (Task 7).

The safety-critical property: wipe_prose removes ONLY crawl-sourced prose and NEVER touches
people (crawler rows with entity_id), the KG (nodes/edges), or manual (dashboard) content.
"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from scripts.rebuild_prose import wipe_prose


def _ki(conn, org_id, created_by, *, entity_id=None, ptype="policy", nk="U"):
    meta = {"natural_key": nk}
    if entity_id is not None:
        meta["entity_id"] = entity_id
    import json
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                 "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
                 (org_id, ptype, "T", "body", json.dumps(meta), nk, created_by))


@pytest.fixture
def conn():
    c = create_all(":memory:")
    return c


def test_wipe_removes_crawl_prose_only(conn):
    org = ensure_org(conn, "njit", "NJIT", None, "university")
    _ki(conn, org, "college_crawl", nk="c1")
    _ki(conn, org, "njit_www_crawl", nk="w1")
    _ki(conn, org, "catalog_crawl", nk="cat1")
    _ki(conn, org, "crawler", entity_id=None, nk="incidental")     # crawler incidental prose -> WIPE
    _ki(conn, org, "crawler", entity_id=42, ptype="profile", nk="person1")  # PERSON -> PRESERVE
    _ki(conn, org, "dashboard", nk="gsa1")                          # manual -> PRESERVE
    conn.execute("INSERT INTO nodes(type,key,name,source,is_active) VALUES('Person','p1','Dr X','crawler',1)")

    res = wipe_prose(conn)

    kept = {r[0] for r in conn.execute(
        "SELECT json_extract(metadata,'$.natural_key') FROM knowledge_items WHERE is_active=1")}
    assert kept == {"person1", "gsa1"}          # only the person row + manual row survive
    assert res["wiped"] == 4
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE is_active=1").fetchone()[0] == 1


def test_wipe_preserves_person_and_kg_counts(conn):
    org = ensure_org(conn, "njit", "NJIT", None, "university")
    for i in range(5):
        _ki(conn, org, "crawler", entity_id=i, ptype="profile", nk=f"person{i}")
    for i in range(3):
        _ki(conn, org, "college_crawl", nk=f"prose{i}")
    conn.execute("INSERT INTO nodes(type,key,name,source,is_active) VALUES('Org','o1','NJIT','crawler',1)")
    conn.execute("INSERT INTO edges(src_id,type,dst_id,source) VALUES(1,'part_of',1,'crawler')")

    before_people = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE created_by='crawler' "
        "AND json_extract(metadata,'$.entity_id') IS NOT NULL").fetchone()[0]
    res = wipe_prose(conn)
    after_people = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE created_by='crawler' "
        "AND json_extract(metadata,'$.entity_id') IS NOT NULL").fetchone()[0]

    assert before_people == after_people == 5
    assert res["wiped"] == 3
    assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1


def test_rebuild_orchestration_smoke(conn):
    # wipe a seeded prose row, run rebuild with empty fake crawls -> no exception, index applied,
    # the seeded crawl-prose row is gone (proves wipe+orchestration wiring, not crawl coverage)
    from scripts.rebuild_prose import rebuild
    org = ensure_org(conn, "njit", "NJIT", None, "university")
    _ki(conn, org, "college_crawl", nk="https://x.njit.edu/old")

    def fetch(u):
        return None

    def fetch_bytes(u):
        return None                     # every sitemap empty -> crawls are no-ops

    out = rebuild(conn, fetch, fetch_bytes)
    assert out["wiped"]["wiped"] == 1
    gone = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
                        "created_by='college_crawl'").fetchone()[0]
    assert gone == 0
    # the prose-scoped unique index was applied
    idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND "
                       "name='idx_prose_canonical'").fetchone()
    assert idx is not None

"""Tests for the global URL-keyed canonical prose upsert (day-1 rebuild Task 4)."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.prose_store import upsert_prose


@pytest.fixture
def conn():
    c = create_all(":memory:")
    return c


@pytest.fixture
def org_a(conn):
    return ensure_org(conn, "orga", "Org A", None, "office")


@pytest.fixture
def org_b(conn):
    return ensure_org(conn, "orgb", "Org B", None, "office")


def _active(conn, canonical):
    return conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
        "AND json_extract(metadata,'$.natural_key')=?", (canonical,)).fetchone()[0]


def test_second_org_same_url_does_not_dup(conn, org_a, org_b):
    # a policy row exists under org A; a THIN webpage capture of the same canonical URL under org B
    # must NOT create a second row and must NOT win
    upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="real " * 100,
                 meta={}, canonical="https://www.njit.edu/p", created_by="college_crawl")
    r = upsert_prose(conn, org_id=org_b, ptype="webpage", title="T", content="nav " * 3,
                     meta={}, canonical="https://www.njit.edu/p", created_by="njit_www_crawl")
    assert r == "skipped_worse"
    assert _active(conn, "https://www.njit.edu/p") == 1
    keep = conn.execute("SELECT type FROM knowledge_items WHERE is_active=1 "
                        "AND json_extract(metadata,'$.natural_key')=?",
                        ("https://www.njit.edu/p",)).fetchone()[0]
    assert keep == "policy"


def test_fuller_replaces_thinner_same_type(conn, org_a):
    upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="a " * 10,
                 meta={}, canonical="U", created_by="c")
    r = upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="a " * 500,
                     meta={}, canonical="U", created_by="c")
    assert r == "updated"
    assert _active(conn, "U") == 1
    kept = conn.execute("SELECT content FROM knowledge_items WHERE is_active=1 "
                        "AND json_extract(metadata,'$.natural_key')=?", ("U",)).fetchone()[0]
    assert kept == "a " * 500


def test_rerun_identical_is_unchanged(conn, org_a):
    upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="body text here",
                 meta={}, canonical="U", created_by="c")
    r = upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="body text here",
                     meta={}, canonical="U", created_by="c")
    assert r == "unchanged"
    assert _active(conn, "U") == 1


def test_new_url_inserts(conn, org_a):
    r = upsert_prose(conn, org_id=org_a, ptype="policy", title="T", content="x y z",
                     meta={}, canonical="https://www.njit.edu/new", created_by="c")
    assert r == "inserted"
    assert _active(conn, "https://www.njit.edu/new") == 1

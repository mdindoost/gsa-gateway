"""Tests for the content-aware fail-closed coverage gate (Task 8)."""
import json
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from scripts.prose_rebuild_gate import coverage_gate


def _prose(conn, nk, content, created_by="njit_www_crawl"):
    org = conn.execute("SELECT id FROM organizations LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                 "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
                 (org, "policy", "T", content, json.dumps({"natural_key": nk}), nk, created_by))


@pytest.fixture
def pair():
    backup = create_all(":memory:")
    rebuilt = create_all(":memory:")
    ensure_org(backup, "njit", "NJIT", None, "university")
    ensure_org(rebuilt, "njit", "NJIT", None, "university")
    return rebuilt, backup


def test_missing_url_fails_closed(pair):
    rebuilt, backup = pair
    _prose(backup, "https://www.njit.edu/a", "real body " * 20)
    _prose(backup, "https://www.njit.edu/b", "real body " * 20)
    _prose(rebuilt, "https://www.njit.edu/a", "real body " * 20)   # b is MISSING from rebuilt
    res = coverage_gate(rebuilt, backup)
    assert res["ok"] is False
    assert "https://www.njit.edu/b" in res["missing_urls"]


def test_thinner_rebuilt_row_fails_closed(pair):
    rebuilt, backup = pair
    _prose(backup, "https://www.njit.edu/a", "real body content " * 50)
    _prose(rebuilt, "https://www.njit.edu/a", "tiny")              # covered but MUCH thinner
    res = coverage_gate(rebuilt, backup)
    assert res["ok"] is False
    assert "https://www.njit.edu/a" in res["thinner_urls"]


def test_covers_all_same_length_passes(pair):
    rebuilt, backup = pair
    for u in ("https://www.njit.edu/a", "https://www.njit.edu/b"):
        _prose(backup, u, "real body " * 20)
        _prose(rebuilt, u, "real body " * 20)
    res = coverage_gate(rebuilt, backup)
    assert res["ok"] is True
    assert res["missing_urls"] == [] and res["thinner_urls"] == []


def test_trailing_slash_alias_is_covered(pair):
    # backup stored the slash form, rebuilt the clean form -> canonicalized both sides -> covered
    rebuilt, backup = pair
    _prose(backup, "https://www.njit.edu/a/", "real body " * 20)
    _prose(rebuilt, "https://www.njit.edu/a", "real body " * 20)
    res = coverage_gate(rebuilt, backup)
    assert res["ok"] is True
    assert res["missing_urls"] == []

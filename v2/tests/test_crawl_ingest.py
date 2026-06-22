"""Tests for per-doc org filing in scripts/_crawl_ingest.py.

njit-web docs default to the 'njit' root org, but a doc may carry an `org:` front-matter slug
(e.g. EOS parking pages → org 'eos'). The org must already exist; a missing slug is an error,
never an auto-guessed org.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._crawl_ingest import _org_id_for, _read_org
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


def test_read_org_from_front_matter():
    assert _read_org('---\ntitle: "X"\nsource_url: "u"\norg: eos\n---\n# X\nbody') == "eos"
    assert _read_org('---\ntitle: "X"\n---\nbody') is None          # no org key
    assert _read_org("# no front matter\nbody") is None


def test_org_id_for_resolves_existing_and_rejects_missing(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    with conn:
        njit = ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        eos = ensure_org(conn, slug="eos", name="EOS", parent_slug="njit", type="office")
    assert _org_id_for(conn, "eos") == eos
    assert _org_id_for(conn, "njit") == njit
    with pytest.raises(ValueError):
        _org_id_for(conn, "does-not-exist")                          # never auto-create

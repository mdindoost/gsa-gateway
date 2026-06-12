"""org_id resolution in the ingest runner (exact-first, slug alias)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# scripts/ is not a package; load the module by path
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "ingest_faculty", REPO_ROOT / "scripts" / "ingest_faculty.py")
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, slug TEXT, type TEXT)")
    c.executemany(
        "INSERT INTO organizations(id,name,slug,type) VALUES(?,?,?,?)",
        [(4, "YWCC", "ywcc", "college"),
         (5, "Computer Science", "computer-science", "department"),
         (6, "Data Science", "data-science", "department"),
         # a decoy that a naive LIKE would wrongly prefer
         (9, "Computer Science and Engineering", "cse", "department")])
    c.commit()
    yield c
    c.close()


def test_exact_department_wins_over_superstring(conn):
    # the OR/LIMIT pitfall: 'Computer Science' must NOT bind to '...and Engineering'
    assert ingest._resolve_org_id(conn, "Computer Science") == 5


def test_college_alias_resolves_to_ywcc(conn):
    assert ingest._resolve_org_id(conn, "Ying Wu College of Computing") == 4


def test_blank_label_returns_none(conn):
    assert ingest._resolve_org_id(conn, "") is None
    assert ingest._resolve_org_id(conn, "   ") is None


def test_unknown_label_returns_none(conn):
    assert ingest._resolve_org_id(conn, "Department of Basket Weaving") is None


def test_retire_legacy_deactivates_only_the_old_card():
    import json
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, source_url TEXT, "
              "metadata TEXT, is_active INTEGER DEFAULT 1, updated_at TEXT)")
    URL = "https://people.njit.edu/profile/ikoutis"
    EID = "people.njit.edu/profile/ikoutis"
    # an old monolithic card (no entity_id) + two new decomposed items (entity_id set)
    old = c.execute("INSERT INTO knowledge_items(source_url,metadata) VALUES(?,?)",
                    (URL, json.dumps({}))).lastrowid
    new1 = c.execute("INSERT INTO knowledge_items(source_url,metadata) VALUES(?,?)",
                     (URL, json.dumps({"entity_id": EID}))).lastrowid
    other = c.execute("INSERT INTO knowledge_items(source_url,metadata) VALUES(?,?)",
                      ("https://people.njit.edu/profile/other", json.dumps({}))).lastrowid
    c.commit()

    retired = ingest._retire_legacy(c, URL, EID)
    assert retired == [old]                                  # only the legacy card
    active = {r["id"] for r in c.execute("SELECT id FROM knowledge_items WHERE is_active=1")}
    assert active == {new1, other}                           # new item + unrelated row untouched
    c.close()

"""Staging extension to upsert_doc_items (NJIT grad-content crawl, task #5).

High-stakes docs ingest as is_active=0 (+ metadata.stakes) so they're held for human
sign-off and invisible to retrieval; re-ingest must not accumulate duplicate staged rows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.gsa_docs import upsert_doc_items


@pytest.fixture
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) VALUES(1,NULL,'Bursar','bursar','office')")
    c.commit()
    yield c
    c.close()


def _rows(conn, slug="refunds"):
    return conn.execute(
        "SELECT id, is_active, json_extract(metadata,'$.stakes') "
        "FROM knowledge_items WHERE json_extract(metadata,'$.doc_id')=?",
        (f"gsa-doc/{slug}",)).fetchall()


def test_high_stakes_doc_is_staged_inactive_and_tagged(conn):
    n = upsert_doc_items(conn, org_id=1, slug="refunds", title="Refund policy",
                         text="# Refunds\nWithdraw after week 3 forfeits 100% of tuition.",
                         source_url="https://njit.edu/bursar/for-students",
                         doc_type="policy", source="njit-crawl",
                         is_active=0, stakes="high")
    conn.commit()
    rows = _rows(conn)
    assert n >= 1 and len(rows) == n
    assert all(r[1] == 0 for r in rows)          # staged → inactive (invisible to retrieval)
    assert all(r[2] == "high" for r in rows)     # tagged high-stakes


def test_live_default_unchanged(conn):
    upsert_doc_items(conn, org_id=1, slug="hours", title="Office hours",
                     text="# Hours\nThe Bursar is open weekdays 9-5 in Fenster Hall.",
                     source_url="https://njit.edu/bursar/contact-us", source="njit-crawl")
    conn.commit()
    rows = _rows(conn, "hours")
    assert rows and all(r[1] == 1 for r in rows)  # live
    assert all(r[2] is None for r in rows)        # no stakes tag


def test_reingest_staged_does_not_accumulate_duplicates(conn):
    for _ in range(3):                            # crawl the same doc 3x
        upsert_doc_items(conn, org_id=1, slug="refunds", title="Refund policy",
                         text="# Refunds\nWithdraw after week 3 forfeits 100% of tuition.",
                         source_url="https://njit.edu/bursar/for-students",
                         source="njit-crawl", is_active=0, stakes="high")
        conn.commit()
    rows = _rows(conn)
    # exactly one staged generation remains (no 3x accumulation) — the idempotency fix
    assert len(rows) >= 1
    assert len({r[0] for r in rows}) == len(rows)
    # count equals a single ingest's chunk count, not 3x
    one = upsert_doc_items(conn, org_id=1, slug="probe", title="x",
                           text="# Refunds\nWithdraw after week 3 forfeits 100% of tuition.",
                           source_url="u", source="njit-crawl", is_active=0, stakes="high")
    assert len(rows) == one


def test_reingest_retires_prior_active(conn):
    upsert_doc_items(conn, org_id=1, slug="hours", title="Hours v1",
                     text="# Hours\nOpen 9-5.", source_url="u", source="njit-crawl")
    upsert_doc_items(conn, org_id=1, slug="hours", title="Hours v2",
                     text="# Hours\nOpen 8-6 now.", source_url="u", source="njit-crawl")
    conn.commit()
    active = [r for r in _rows(conn, "hours") if r[1] == 1]
    contents = conn.execute(
        "SELECT content FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.doc_id')='gsa-doc/hours'").fetchall()
    assert all("8-6" in c[0] for c in contents)   # only the new version is live

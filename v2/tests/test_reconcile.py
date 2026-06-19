"""Item-level reconcile: re-crawling an entity applies a minimal diff."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.database.schema import create_all
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord, Publication
from v2.core.ingestion.reconcile import reconcile_entity

EID = "people.njit.edu/profile/ikoutis"


def rec(pubs, research="Spectral graph theory.", verified=True):
    return EntityRecord(
        entity_id=EID, name="Ioannis Koutis", org="Computer Science",
        source_url="https://people.njit.edu/profile/ikoutis", verified=verified,
        titles=["Associate Professor"], research_statement=research,
        publications=[Publication(t, "ICALP", "2018") for t in pubs],
    )


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','custom')")
    c.commit()
    yield c
    c.close()


def _active(conn):
    return conn.execute(
        "SELECT id,type,title,content,version,root_id,parent_id,metadata "
        "FROM knowledge_items WHERE org_id=1 AND is_active=1 "
        "AND json_extract(metadata,'$.entity_id')=?", (EID,)).fetchall()


def ingest(conn, record):
    return reconcile_entity(conn, 1, EID, decompose(record))


def test_first_ingest_inserts_everything():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','custom')")
    items = decompose(rec(["P1", "P2"]))
    res = reconcile_entity(c, 1, EID, items)
    assert len(res.inserted_ids) == len(items)
    assert res.superseded == [] and res.deactivated_ids == []
    assert sorted(res.to_embed) == sorted(res.inserted_ids)
    c.close()


def test_reingest_identical_is_all_noop(conn):
    ingest(conn, rec(["P1", "P2"]))
    res = ingest(conn, rec(["P1", "P2"]))
    assert res.inserted_ids == [] and res.superseded == [] and res.deactivated_ids == []
    assert len(res.unchanged_ids) == len(_active(conn))
    assert res.to_embed == [] and res.vectors_to_drop == []


def test_changed_item_version_bumps_with_lineage(conn):
    ingest(conn, rec(["P1"], research="Old statement."))
    before = {r["type"]: r for r in _active(conn)}
    old_rs = before["research_statement"]

    res = ingest(conn, rec(["P1"], research="New statement."))
    assert len(res.superseded) == 1
    old_id, new_id = res.superseded[0]
    assert old_id == old_rs["id"]

    after = {r["type"]: r for r in _active(conn)}
    new_rs = after["research_statement"]
    assert new_rs["id"] == new_id
    assert new_rs["version"] == 2
    assert new_rs["parent_id"] == old_id          # previous version
    assert new_rs["root_id"] == old_rs["root_id"] # same version group
    assert "New statement" in new_rs["content"]
    # only the research_statement moved; publication untouched
    assert after["publication"]["id"] == before["publication"]["id"]
    assert res.vectors_to_drop == [old_id] and res.to_embed == [new_id]


def test_added_publication_is_inserted(conn):
    ingest(conn, rec(["P1"]))
    res = ingest(conn, rec(["P1", "P2"]))
    assert len(res.inserted_ids) == 1
    assert res.superseded == [] and res.deactivated_ids == []
    titles = {r["title"] for r in _active(conn) if r["type"] == "publication"}
    assert titles == {"P1", "P2"}


def test_dropped_publication_is_deactivated(conn):
    ingest(conn, rec(["P1", "P2"]))
    res = ingest(conn, rec(["P1"]))
    assert len(res.deactivated_ids) == 1
    assert res.inserted_ids == [] and res.superseded == []
    titles = {r["title"] for r in _active(conn) if r["type"] == "publication"}
    assert titles == {"P1"}
    assert res.vectors_to_drop == res.deactivated_ids


def test_reconcile_is_scoped_by_created_by(conn):
    # A crawler reconcile must only diff/deactivate ITS OWN source's items. An
    # enrichment item (created_by='scholar') sharing the same entity_id+org must
    # survive a re-crawl whose fresh set never mentions it.
    reconcile_entity(conn, 1, EID, decompose(rec(["P1"])), created_by="crawler")
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by,version,is_active) "
        "VALUES(?,?,?,?,?,?,1,1)",
        (1, "scholar_profile", "Scholar metrics", "4000 citations, h-index 30",
         json.dumps({"entity_id": EID, "natural_key": EID + ":scholar:metrics"}), "scholar"),
    )
    conn.commit()

    # Re-crawl: the fresh crawler set has no scholar item — must NOT touch it.
    res = reconcile_entity(conn, 1, EID, decompose(rec(["P1"])), created_by="crawler")
    survived = conn.execute(
        "SELECT is_active FROM knowledge_items "
        "WHERE json_extract(metadata,'$.natural_key')=?", (EID + ":scholar:metrics",)).fetchone()
    assert survived["is_active"] == 1
    assert res.deactivated_ids == []


def test_empty_items_does_not_deactivate_existing(conn):
    # A freshly-parsed person whose decomposition comes back empty (transient partial
    # fetch / parse anomaly) must NOT have their whole bio retired — they're still present.
    ingest(conn, rec(["P1", "P2"]))
    active_before = {r["id"] for r in _active(conn)}
    assert active_before  # sanity

    res = reconcile_entity(conn, 1, EID, [])  # empty decomposition
    assert res.deactivated_ids == []
    assert res.inserted_ids == [] and res.superseded == []
    assert {r["id"] for r in _active(conn)} == active_before


def test_natural_key_is_persisted(conn):
    ingest(conn, rec(["P1"]))
    for r in _active(conn):
        meta = json.loads(r["metadata"])
        assert meta["natural_key"].startswith(EID + ":")
        assert meta["entity_id"] == EID


def test_verified_flip_versions_the_profile(conn):
    ingest(conn, rec(["P1"], verified=True))
    res = ingest(conn, rec(["P1"], verified=False))
    # every item's metadata.verified changed -> every item version-bumps
    assert res.inserted_ids == [] and res.deactivated_ids == []
    assert len(res.superseded) == len(_active(conn))
    for r in _active(conn):
        assert json.loads(r["metadata"])["verified"] is False

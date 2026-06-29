"""A4 — end-to-end recrawl cycle, offline & deterministic.

Proves the full additive cycle the rebuild relies on, built from the component
functions (no network, no Ollama): a fake page fetcher returns page v1 then a
changed page v2, and a fake embedder stands in for Ollama. The test asserts:

  crawl v1 → build chunks            → invariant True, corpus_build_ready True
  recrawl changed page → reconcile   → old item's chunks dropped (A2)
  re-embed                           → invariant still True, old chunk text gone,
                                       new content chunked & findable

This is the gate that proves A1–A3 compose into a consistent recrawl. The
"fetcher" is a callable returning page text; the crawl step turns that text into
a KItem and reconciles it exactly as the real ingest path does.
"""
from __future__ import annotations

from v2.core.database.schema import create_all
from v2.core.database.vector_gc import assert_chunk_invariant, corpus_build_ready
from v2.core.ingestion.entity import KItem
from v2.core.ingestion.reconcile import reconcile_entity
from v2.core.retrieval.chunk_populate import populate_item_chunks
from v2.core.retrieval.model_descriptor import active_descriptor

D = active_descriptor()

ENTITY = "https://computing.njit.edu/office-hours"
PAGE_V1 = ("Registrar office hours are Monday to Friday, 9am to 5pm. "
           "Transcript requests are processed within two weeks.")
PAGE_V2 = ("Registrar office hours are Monday to Thursday, 8am to 6pm. "
           "Transcript requests are processed within three business days.")


def _fake_fetch(version: str) -> str:
    """Stubbed page fetcher: returns page v1 first, the changed page v2 after."""
    return PAGE_V1 if version == "v1" else PAGE_V2


def _fake_embed(_text):
    """Deterministic offline embedder (a fixed unit vector of descriptor width)."""
    return [1.0] + [0.0] * (D.dim - 1)


def _kitem_from_page(content: str) -> KItem:
    # One page → one item with a stable natural_key, as a prose crawl would.
    return KItem(type="policy", title="Office Hours", content=content,
                 natural_key="office:hours", metadata={"entity_id": ENTITY},
                 source_url=ENTITY)


def _crawl(conn, version: str):
    """Fetch a page version, reconcile it as the new state of the entity."""
    page = _fake_fetch(version)
    return reconcile_entity(conn, 1, ENTITY, [_kitem_from_page(page)],
                            created_by="college_crawl")


def _embed_pass(conn):
    """Chunk + embed every active served item missing chunks (embed_chunks, offline)."""
    rows = conn.execute(
        "SELECT id FROM knowledge_items WHERE is_active=1 "
        "AND id NOT IN (SELECT DISTINCT parent_id FROM knowledge_chunks)"
    ).fetchall()
    for r in rows:
        populate_item_chunks(conn, r["id"], _fake_embed, D)
    conn.commit()


def _all_chunk_text(conn) -> str:
    return " ".join(r[0] for r in conn.execute("SELECT text FROM knowledge_chunks"))


def _seed_org(conn):
    conn.execute(
        "INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")


def test_recrawl_additive_cycle(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    _seed_org(conn)

    # 1. Crawl v1 → build chunks.
    res1 = _crawl(conn, "v1")
    assert res1.inserted_ids, "v1 crawl should insert a fresh item"
    v1_id = res1.inserted_ids[0]
    _embed_pass(conn)
    assert_chunk_invariant(conn, D)
    assert corpus_build_ready(conn, D) is True
    assert "two weeks" in _all_chunk_text(conn)

    # 2. Recrawl the changed page → reconcile supersedes the old item.
    res2 = _crawl(conn, "v2")
    assert res2.superseded, "changed page should version-bump (supersede) the item"
    old_id, new_id = res2.superseded[0]
    assert old_id == v1_id

    # 3. A2: the superseded item's chunks are dropped (no orphans).
    assert conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=?", (old_id,)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunk_vectors WHERE parent_id=?", (old_id,)).fetchone()[0] == 0

    # 4. Re-embed → corpus consistent again, new content present, old content gone.
    _embed_pass(conn)
    assert_chunk_invariant(conn, D)
    assert corpus_build_ready(conn, D) is True
    final = _all_chunk_text(conn)
    assert "three business days" in final
    assert "two weeks" not in final
    # The new content is chunked under the new (active) parent.
    assert conn.execute(
        "SELECT COUNT(*) FROM knowledge_chunks WHERE parent_id=?", (new_id,)).fetchone()[0] > 0

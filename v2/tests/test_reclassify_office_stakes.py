# v2/tests/test_reclassify_office_stakes.py
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from scripts._reclassify_office_stakes import reclassify

GEN = "https://www.njit.edu/parking/2026-summer-hours"
HI  = "https://www.njit.edu/bursar/payment-options"


def _ins(conn, oid, url, stakes, active):
    meta = {"doc_id": "gsa-doc/x", "verified": True}
    if stakes: meta["stakes"] = stakes
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,is_active,created_by)"
                 " VALUES(?,?,?,?,?,?,?,'crawler')", (oid, "office_page", "t", "body", json.dumps(meta), url, active))


def test_reclassify_activates_generic_and_tags_high(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        _ins(conn, oid, GEN, "high", 0); _ins(conn, oid, GEN, "high", 0)   # 2 chunks, staged
        _ins(conn, oid, HI, "high", 0)                                     # 1 chunk, staged
        reclassify(conn, {GEN: "generic", HI: "high"})
    g = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (GEN,)).fetchall()
    assert all(r["is_active"] == 1 and r["s"] is None for r in g)          # generic: active, no stakes
    h = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()
    assert h["is_active"] == 1 and h["s"] == "high"                        # high: active, stakes kept
    # metadata not clobbered (HI):
    assert conn.execute("SELECT json_extract(metadata,'$.doc_id') FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()[0] == "gsa-doc/x"
    # metadata not clobbered (GEN): json_remove must not touch doc_id
    assert conn.execute("SELECT json_extract(metadata,'$.doc_id') FROM knowledge_items WHERE source_url=? LIMIT 1", (GEN,)).fetchone()[0] == "gsa-doc/x"


def test_generic_only_skips_high(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        _ins(conn, oid, GEN, "high", 0)   # generic page, staged
        _ins(conn, oid, HI,  "high", 0)   # high page, staged
        reclassify(conn, {GEN: "generic", HI: "high"}, generic_only=True)
    g = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (GEN,)).fetchone()
    assert g["is_active"] == 1 and g["s"] is None   # generic: activated, stakes dropped
    h = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()
    assert h["is_active"] == 0 and h["s"] == "high"  # high: UNTOUCHED — still staged for Plan 2


def test_reclassify_is_idempotent(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        _ins(conn, oid, HI, "high", 0)
        reclassify(conn, {HI: "high"}); reclassify(conn, {HI: "high"})     # twice
    r = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()
    assert r["is_active"] == 1 and r["s"] == "high"

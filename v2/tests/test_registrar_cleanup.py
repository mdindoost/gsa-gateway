import importlib
import sys
from pathlib import Path

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


def _seed():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "registrar", "Office of the Registrar", "njit", "office")

    def ins(cb, title, url, typ="policy"):
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(?,?,?,?,'{}',?,1,1,?)",
                     (oid, typ, title, "c", url, cb))

    HOME = "https://www.njit.edu/registrar/"
    WD = "https://www.njit.edu/registrar/withdrawal"
    # the new crawler source of truth — KEPT
    ins("crawler", "Office of the Registrar", HOME)
    ins("crawler", "Withdrawal", WD)
    # superseded njit-crawl rows (incl. dupes) — RETIRE
    ins("njit-crawl", "Registrar — Withdrawal", WD)
    ins("njit-crawl", "Registrar — Withdrawal", WD)
    ins("njit-crawl", "Registrar — Home", HOME)
    # the dashboard homepage stub (type='contact', on a /registrar URL) — RETIRE
    ins("dashboard", "Office of the Registrar", HOME, typ="contact")
    # a genuinely-manual dashboard row NOT on a registrar URL — KEPT
    ins("dashboard", "Internal note", "https://example.org/manual")
    conn.commit()
    return conn, oid


def _mig():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    return importlib.import_module("scripts._registrar_cleanup_migrate")


def test_retire_set_supersedes_njitcrawl_and_dashboard_stub_keeps_crawler_and_manual():
    conn, oid = _seed()
    mig = _mig()
    retire = mig.select_retire(conn)
    titles = {r["title"] for r in retire}
    cbs = {r["created_by"] for r in retire}
    # all 3 njit-crawl + the dashboard /registrar contact stub are retired
    assert cbs == {"njit-crawl", "dashboard"}
    assert len(retire) == 4
    # crawler rows + the off-site manual dashboard row are NOT retired
    kept_urls = {r["source_url"] for r in retire}
    assert "https://example.org/manual" not in kept_urls


def test_dashboard_stub_kept_when_no_crawler_rows_present():
    """Safety guard: if the crawl has NOT run (no crawler rows), a dashboard row is never
    retired — a mistaken pre-crawl run can't strip manual data."""
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "registrar", "Office of the Registrar", "njit", "office")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                 "version,is_active,created_by) VALUES(?,?,?,?,'{}',?,1,1,?)",
                 (oid, "contact", "Office of the Registrar", "c",
                  "https://www.njit.edu/registrar/", "dashboard"))
    conn.commit()
    mig = _mig()
    retire = mig.select_retire(conn)
    assert all(r["created_by"] != "dashboard" for r in retire)

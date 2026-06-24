import importlib
import sys
from pathlib import Path

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


def _seed():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "bursar", "Office of the Bursar / Student Accounts", "njit", "office")

    def ins(cb, title, url, typ="policy"):
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(?,?,?,?,'{}',?,1,1,?)",
                     (oid, typ, title, "c", url, cb))

    HOME = "https://www.njit.edu/bursar/"
    FORMS = "https://www.njit.edu/bursar/forms"
    # the new crawler source of truth — KEPT
    ins("crawler", "Office of the Bursar", HOME)
    ins("crawler", "Forms", FORMS)
    # superseded njit-crawl rows — RETIRE (note: dup eRefund-style rows)
    ins("njit-crawl", "Bursar — Forms", FORMS)
    ins("njit-crawl", "Bursar — FAQs", "https://www.njit.edu/bursar/faqs")
    # the dashboard homepage stub is type='contact' (NOT policy) — must still be RETIRED by source+key
    ins("dashboard", "Office of the Bursar / Student Accounts", HOME, typ="contact")
    # genuinely manual rows (no bursar-site URL) — KEPT
    ins("dashboard", "Hand-written note", "https://internal/manual-only")
    ins("dashboard", "Manual note", None)
    # forward-safety: an unrelated org's /bursar-foo path must NOT be over-matched by the anchor
    ins("dashboard", "Other office page", "https://www.njit.edu/bursar-foo/x")
    conn.commit()
    return conn


def test_select_retire():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._bursar_cleanup_migrate")
    conn = _seed()
    retire = mod.select_retire(conn)
    retired_urls = {r["source_url"] for r in retire}

    # njit-crawl superseded
    assert sum(1 for r in retire if r["created_by"] == "njit-crawl") == 2
    # the type='contact' dashboard homepage stub is retired (matched by source+key, not type)
    assert "https://www.njit.edu/bursar/" in retired_urls
    assert any(r["created_by"] == "dashboard" and r["source_url"] == "https://www.njit.edu/bursar/"
               for r in retire)
    # genuinely manual rows survive
    assert "https://internal/manual-only" not in retired_urls
    assert None not in retired_urls
    # anchored matcher does NOT over-match /bursar-foo
    assert "https://www.njit.edu/bursar-foo/x" not in retired_urls
    # crawler rows never retired
    assert all(r["created_by"] != "crawler" for r in retire)


def test_is_bursar_site_url_anchored():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._bursar_cleanup_migrate")
    assert mod._is_bursar_site_url("https://www.njit.edu/bursar/")
    assert mod._is_bursar_site_url("https://www.njit.edu/bursar/node/71")
    assert mod._is_bursar_site_url("https://www.njit.edu/bursar/1098-t.php")
    assert not mod._is_bursar_site_url("https://www.njit.edu/bursar-foo/x")   # anchor guard
    assert not mod._is_bursar_site_url("https://internal/manual-only")
    assert not mod._is_bursar_site_url(None)

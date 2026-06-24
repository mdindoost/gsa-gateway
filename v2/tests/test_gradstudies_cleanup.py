import importlib
import sys
from pathlib import Path

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


def _seed():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "graduate-studies", "Graduate Studies", "njit", "office")

    def ins(cb, title, url, src=None):
        meta = '{"source":"%s"}' % src if src else "{}"
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
                     (oid, "policy", title, "c", meta, url, cb))

    ins("migration", "stub", "https://graduatestudies.njit.edu")                    # retire: dead stub
    ins("njit-crawl", "OGS — Forms", "https://www.njit.edu/graduatestudies/forms")  # retire: superseded
    for _ in range(4):
        ins("dashboard", "Ph.D. Credit Requirements",
            "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements")  # dedup 3
    ins("dashboard", "Hand-written GSA note", "https://internal/manual-only")        # KEEP: not on site
    ins("crawler", "Forms", "https://www.njit.edu/graduatestudies/forms")            # KEEP: new source
    conn.commit()
    return conn


def test_select_retire():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._gradstudies_cleanup_migrate")
    conn = _seed()
    retire = mod.select_retire(conn)
    reasons = {(r["created_by"], r["reason"]) for r in retire}
    assert ("migration", "dead-subdomain-stub") in reasons
    assert ("njit-crawl", "superseded-by-crawler") in reasons
    assert sum(1 for r in retire if r["created_by"] == "dashboard") == 3   # dedup keeps 1 of 4
    # never retire the manual-only row or any crawler row
    assert all(r["source_url"] != "https://internal/manual-only" for r in retire)
    assert all(r["created_by"] != "crawler" for r in retire)

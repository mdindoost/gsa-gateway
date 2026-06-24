import importlib
import json
import sys
from pathlib import Path

from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment


def _seed():
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "graduate-studies", "Graduate Studies", "njit", "office")

    def ins(cb, title, url, src=None):
        meta = '{"source":"%s"}' % src if src else "{}"
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
                     (oid, "policy", title, "c", meta, url, cb))

    PHD = "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements"
    FORMS = "https://www.njit.edu/graduatestudies/forms"
    # the new crawler source of truth (whole pages) — KEPT
    ins("crawler", "PhD Credit Requirements", PHD)
    ins("crawler", "Forms", FORMS)
    # stale rows the crawler supersedes — RETIRE
    ins("migration", "stub", "https://graduatestudies.njit.edu")                    # dead subdomain
    ins("njit-crawl", "OGS — Forms", FORMS)                                          # superseded
    for _ in range(4):                                                               # 4 partial chunks
        ins("dashboard", "Ph.D. Credit Requirements", PHD)                          # of a crawler page
    # genuinely manual rows (no crawler overlap) — KEPT
    ins("dashboard", "Hand-written GSA note", "https://internal/manual-only")        # off-site URL
    ins("dashboard", "Another manual note", None)                                   # NULL url (manual)
    conn.commit()
    return conn


def test_select_retire():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._gradstudies_cleanup_migrate")
    conn = _seed()
    retire = mod.select_retire(conn)
    reasons = {(r["created_by"], r["reason"]) for r in retire}
    retired_urls = {r["source_url"] for r in retire}
    assert ("migration", "dead-subdomain-stub") in reasons
    assert ("njit-crawl", "superseded-by-crawler") in reasons
    # ALL 4 dashboard chunks on the crawler-covered PhD page are retired (not just 3 of 4)
    assert sum(1 for r in retire if r["created_by"] == "dashboard") == 4
    # genuinely manual rows survive: off-site URL and NULL url
    assert "https://internal/manual-only" not in retired_urls
    assert None not in retired_urls                       # NULL-url manual row never dedup-collided
    # crawler rows are never retired
    assert all(r["created_by"] != "crawler" for r in retire)


def test_select_retire_people():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._gradstudies_cleanup_migrate")
    conn = create_all(":memory:")
    ensure_org(conn, "njit", "New Jersey Institute of Technology", None, "university")
    oid = ensure_org(conn, "graduate-studies", "Graduate Studies", "njit", "office")
    sync_org_nodes(conn)

    def add_person(key, name, email):
        pid = project_appointment(conn, person_key=key, name=name, org_id=oid,
                                  category="staff", titles=["X"], source_section="contacts",
                                  source=key.split("/")[0])
        conn.execute("UPDATE nodes SET attrs=? WHERE id=?", (json.dumps({"email": email}), pid))
        return pid

    # crawler set (the new source of truth)
    add_person("crawler/graduate-studies/ester-flaim", "Ester Flaim", "ester.flaim@njit.edu")
    # dashboard dupe (same email) → retire ; dashboard non-dupe (no crawler match) → KEEP
    dupe = add_person("dashboard/graduate-studies/ester-flaim", "Ester Flaim", "ester.flaim@njit.edu")
    keep = add_person("dashboard/graduate-studies/jane-manual", "Jane Manual", "jane.manual@njit.edu")
    conn.commit()

    people = mod.select_retire_people(conn)
    ids = {p["person_id"] for p in people}
    assert dupe in ids                      # email matches a crawler person → superseded
    assert keep not in ids                  # no crawler match → left for manual review
    assert all(p["reason"] == "superseded-by-crawler" for p in people)

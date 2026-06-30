import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.college_crawl import ingest_college, EntryResult
from v2.core.ingestion.eos_crawl import ProsePage


def _conn():
    c = create_all(":memory:")

    def _row_factory(cursor, row):
        fields = [d[0] for d in cursor.description]

        class _R(tuple):
            def __getitem__(self, k):
                return super().__getitem__(fields.index(k) if isinstance(k, str) else k)

        return _R(row)

    c.row_factory = _row_factory
    return c


def _result(url, content, title="T"):
    p = ProsePage(title=title, content=content, source_url=url)
    r = EntryResult(seed="www", prose=[p], skipped=[])
    r.html_by_url[url] = "<html></html>"
    return r


# --- Step 1: force_type override on ingest_college (additive seam) -----------------

def test_force_type_overrides_classify_type():
    conn = _conn()
    # a /news URL would normally classify_type -> 'news'; force_type='webpage' overrides it
    url = "https://www.njit.edu/academics/degree/phd-computer-science"
    ingest_college(conn, "njit", "NJIT", None, _result(url, "marketing body"),
                   {url: "<html></html>"}, org_type="university",
                   created_by="njit_www_crawl", force_type="webpage")
    typ = conn.execute("SELECT type FROM knowledge_items WHERE is_active=1 AND source_url=?",
                       (url,)).fetchone()[0]
    assert typ == "webpage"


def test_force_type_none_keeps_classify_type():
    conn = _conn()
    # default force_type=None -> classify_type: a /news segment -> 'news'
    url = "https://www.njit.edu/president/news/welcome-fall"
    ingest_college(conn, "njit", "NJIT", None, _result(url, "news body"),
                   {url: "<html></html>"}, org_type="university", created_by="njit_www_crawl")
    typ = conn.execute("SELECT type FROM knowledge_items WHERE is_active=1 AND source_url=?",
                       (url,)).fetchone()[0]
    assert typ == "news"


# --- Step 2: reconcile_sitemap_set (generic created_by/types + SE-2 seen_hashes) ---

def _seed_org(conn):
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")


def _ins(conn, url, typ, created_by, content_hash="h", active=1):
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,version,is_active,"
        "created_by) VALUES(1,?,?,?,?,?,1,?,?)",
        (typ, "t", "c", '{"content_hash":"%s"}' % content_hash, url, active, created_by))


def _active(conn, url):
    return conn.execute("SELECT is_active FROM knowledge_items WHERE source_url=?", (url,)).fetchone()[0]


def test_reconcile_sitemap_set_retires_left_union_keeps_pdf_and_other_sources():
    from v2.core.ingestion.catalog_crawl import reconcile_sitemap_set
    conn = _conn(); _seed_org(conn)
    _ins(conn, "https://www.njit.edu/keep", "policy", "njit_www_crawl")
    _ins(conn, "https://www.njit.edu/gone", "news", "njit_www_crawl")       # news also reconciled (types)
    _ins(conn, "https://www.njit.edu/file.pdf", "pdf", "njit_www_crawl")    # pdf never retired (B2)
    _ins(conn, "https://www.njit.edu/office", "policy", "crawler")          # other source — never touched
    union = ["https://www.njit.edu/keep"] + [f"https://www.njit.edu/p{i}" for i in range(400)]
    out = reconcile_sitemap_set(conn, union, prior_active_count=2,
                                created_by="njit_www_crawl", types=("policy", "news", "event"))
    assert out["retired"] == 1
    assert _active(conn, "https://www.njit.edu/gone") == 0
    assert _active(conn, "https://www.njit.edu/file.pdf") == 1   # pdf kept
    assert _active(conn, "https://www.njit.edu/office") == 1     # isolation: other created_by untouched


def test_reconcile_sitemap_set_seen_hashes_guard_prevents_rename_content_loss():
    # SE-2: a row whose URL left the union but whose content was seen THIS run (renamed/aliased)
    # must NOT be retired — else dedup-dropped re-insert + retire = content lost.
    from v2.core.ingestion.catalog_crawl import reconcile_sitemap_set
    conn = _conn(); _seed_org(conn)
    _ins(conn, "https://www.njit.edu/old-slug", "policy", "njit_www_crawl", content_hash="CC")
    union = [f"https://www.njit.edu/p{i}" for i in range(400)]   # old-slug NOT in union
    out = reconcile_sitemap_set(conn, union, prior_active_count=1, created_by="njit_www_crawl",
                                seen_hashes={"CC"}, types=("policy", "news", "event"))
    assert out["retired"] == 0
    assert _active(conn, "https://www.njit.edu/old-slug") == 1   # content survives this run → kept


def test_reconcile_sitemap_set_empty_and_floor_guards():
    from v2.core.ingestion.catalog_crawl import reconcile_sitemap_set
    conn = _conn(); _seed_org(conn)
    _ins(conn, "https://www.njit.edu/x", "policy", "njit_www_crawl")
    assert reconcile_sitemap_set(conn, [], prior_active_count=446,
                                 created_by="njit_www_crawl")["retired"] == 0          # empty → skip
    assert reconcile_sitemap_set(conn, [f"u{i}" for i in range(50)], prior_active_count=446,
                                 created_by="njit_www_crawl")["retired"] == 0           # 50<floor → skip
    assert _active(conn, "https://www.njit.edu/x") == 1


# --- Step 3: www_crawl module --------------------------------------------------------

_MAIN_HTML = "<html><body><div role='main'><h1>{t}</h1><p>{b}</p></div></body></html>"


def test_registry_well_formed_office_and_service_and_main():
    from v2.core.ingestion import www_crawl as W
    assert W.WWW_SUBSITES, "registry must be non-empty"
    slugs = {e.org_slug for e in W.WWW_SUBSITES}
    # offices map to their real existing slugs
    assert {"bursar", "registrar", "career-development", "dean-of-students",
            "graduate-studies", "ogi", "graduate-admissions", "eos"} <= slugs
    # service subsites are type='office' under njit
    svc = [e for e in W.WWW_SUBSITES if e.org_slug in
           ("policies", "finance", "president", "provost", "reslife", "publicsafety")]
    assert svc and all(e.org_type == "office" and e.parent_slug == "njit" for e in svc)
    # every entry has a real subsite sitemap URL
    assert all(e.sitemap_url.startswith("https://www.njit.edu/") and
               e.sitemap_url.endswith("sitemap.xml") for e in W.WWW_SUBSITES)
    # exactly the main-sitemap entry carries page_type='webpage'
    webentries = [e for e in W.WWW_SUBSITES if e.page_type == "webpage"]
    assert len(webentries) == 1 and webentries[0].sitemap_url == W.MAIN_SITEMAP


def test_www_seed_urls_parses_sitemap():
    from v2.core.ingestion.www_crawl import www_seed_urls
    xml = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           b'<url><loc>https://www.njit.edu/bursar/payment-information/</loc></url>'
           b'<url><loc>https://www.njit.edu/bursar/faqs</loc></url></urlset>')
    out = www_seed_urls(lambda u: xml, "https://www.njit.edu/bursar/sitemap.xml")
    assert out == ["https://www.njit.edu/bursar/payment-information",
                   "https://www.njit.edu/bursar/faqs"]


def test_filter_existing_content_dedups_and_grows_set():
    from v2.core.ingestion.www_crawl import filter_existing_content, _content_hash
    from v2.core.ingestion.eos_crawl import ProsePage
    dup = ProsePage(title="Dup", content="ALREADY HERE", source_url="https://www.njit.edu/a")
    new = ProsePage(title="New", content="BRAND NEW", source_url="https://www.njit.edu/b")
    res = EntryResult(seed="www", prose=[dup, new], skipped=[])
    res.html_by_url = {dup.source_url: "<x>", new.source_url: "<y>"}
    existing = {_content_hash("ALREADY HERE")}
    dropped = filter_existing_content(existing, res)
    assert dropped == 1
    assert [p.source_url for p in res.prose] == ["https://www.njit.edu/b"]   # dup removed
    assert dup.source_url not in res.html_by_url                              # html released
    assert _content_hash("BRAND NEW") in existing                            # kept hash added (within-run dedup)


def test_detect_stale_dups_flags_title_match_divergent_hash_other_source():
    from v2.core.ingestion.www_crawl import detect_stale_dups, _content_hash
    from v2.core.ingestion.eos_crawl import ProsePage
    conn = _conn(); _seed_org(conn)
    # an OFFICE row (different source) with the same title but OLD content
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,version,"
                 "is_active,created_by) VALUES(1,'policy','Payment Information','old $200',?,"
                 "'https://www.njit.edu/bursar/node/71',1,1,'crawler')",
                 ('{"content_hash":"%s"}' % _content_hash("old $200"),))
    cur = ProsePage(title="Payment Information", content="new $250",
                    source_url="https://www.njit.edu/bursar/payment-information")
    same = ProsePage(title="Other", content="x", source_url="https://www.njit.edu/bursar/other")
    warns = detect_stale_dups(conn, 1, [cur, same], "njit_www_crawl")
    assert len(warns) == 1 and "Payment Information" in warns[0]


def _fake_fetchers(sitemaps, pages):
    """sitemaps: {sitemap_url: [page_urls] or None}; pages: {url: html or None}."""
    def fetch_bytes(u):
        locs = sitemaps.get(u)
        if locs is None:
            return None
        body = "".join(f"<url><loc>{x}</loc></url>" for x in locs)
        return (f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f'{body}</urlset>').encode()
    def fetch(u):
        return pages.get(u)
    return fetch, fetch_bytes


def _entry(**kw):
    from v2.core.ingestion.www_crawl import WwwEntry
    base = dict(sitemap_url="https://www.njit.edu/bursar/sitemap.xml", org_slug="bursar",
                org_name="Bursar", parent_slug="njit", org_type="office", page_type=None)
    base.update(kw)
    return WwwEntry(**base)


def test_run_ingests_under_source_with_force_type_and_dedup():
    from v2.core.ingestion import www_crawl as W
    conn = _conn()
    # pre-existing OFFICE row whose content the www crawl will re-encounter → must dedup (skip)
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
    main_entry = _entry(sitemap_url=W.MAIN_SITEMAP, org_slug="njit", org_name="NJIT",
                        parent_slug=None, org_type="university", page_type="webpage")
    bursar_entry = _entry()
    dup_url = "https://www.njit.edu/bursar/dup"
    new_url = "https://www.njit.edu/bursar/payment-information"
    market_url = "https://www.njit.edu/academics/degree/phd-cs"
    dup_html = _MAIN_HTML.format(t="Dup", b="DUPBODY")
    # The office crawler stores the hash of the EXTRACTED content (same extract_prose as www_crawl),
    # so seed the office dup row with the extracted-content hash of the SAME html the crawl will see.
    from v2.core.ingestion.www_crawl import _content_hash
    from v2.core.ingestion.eos_crawl import extract_prose
    dup_extracted = extract_prose(dup_url, dup_html).content
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,version,"
                 "is_active,created_by) VALUES(1,'policy','Dup',?,?,?,1,1,'crawler')",
                 (dup_extracted, '{"content_hash":"%s"}' % _content_hash(dup_extracted),
                  "https://www.njit.edu/bursar/node/9"))
    sitemaps = {bursar_entry.sitemap_url: [dup_url, new_url], W.MAIN_SITEMAP: [market_url]}
    pages = {
        dup_url: dup_html,                                               # identical to office → dropped
        new_url: _MAIN_HTML.format(t="Payment Information", b="pay $250 plan"),
        market_url: _MAIN_HTML.format(t="PhD CS", b="career salary marketing"),
    }
    fetch, fetch_bytes = _fake_fetchers(sitemaps, pages)
    out = W.run(conn, fetch, fetch_bytes, entries=[bursar_entry, main_entry])
    rows = dict(conn.execute(
        "SELECT source_url, type FROM knowledge_items WHERE is_active=1 AND created_by='njit_www_crawl'").fetchall())
    assert new_url in rows and rows[new_url] == "policy"          # office gap filled, classify_type
    assert market_url in rows and rows[market_url] == "webpage"   # main bucket force_type
    assert dup_url not in rows                                    # deduped against the office row
    assert out["totals"]["dropped_dup"] == 1


def test_run_skips_reconcile_when_any_subsite_sitemap_fails():
    from v2.core.ingestion import www_crawl as W
    conn = _conn()
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
    # pre-existing www row that WOULD be retired if reconcile ran (its url is not in any union)
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,version,"
                 "is_active,created_by) VALUES(1,'policy','Old','old',?,"
                 "'https://www.njit.edu/bursar/old',1,1,'njit_www_crawl')",
                 ('{"content_hash":"oldhash"}',))
    good = _entry(sitemap_url="https://www.njit.edu/registrar/sitemap.xml", org_slug="registrar",
                  org_name="Reg", parent_slug="njit", org_type="office")
    bad = _entry()  # bursar sitemap returns None → failure
    big = [f"https://www.njit.edu/registrar/p{i}" for i in range(400)]
    sitemaps = {good.sitemap_url: big, bad.sitemap_url: None}
    pages = {u: _MAIN_HTML.format(t="P", b=f"body{u}") for u in big}
    fetch, fetch_bytes = _fake_fetchers(sitemaps, pages)
    out = W.run(conn, fetch, fetch_bytes, entries=[good, bad])
    # SE-1: a failed subsite sitemap → retirement skipped entirely → the old row survives
    assert out["reconcile"]["skipped_reason"] == "subsite_sitemap_failed"
    assert _active(conn, "https://www.njit.edu/bursar/old") == 1


def test_run_limit_truncates_per_entry_and_skips_reconcile():
    from v2.core.ingestion import www_crawl as W
    conn = _conn()
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES(1,'njit','NJIT','university')")
    e = _entry()
    urls = [f"https://www.njit.edu/bursar/p{i}" for i in range(5)]
    sitemaps = {e.sitemap_url: urls}
    pages = {u: _MAIN_HTML.format(t=u[-2:], b=f"body {u}") for u in urls}
    fetch, fetch_bytes = _fake_fetchers(sitemaps, pages)
    out = W.run(conn, fetch, fetch_bytes, entries=[e], limit=2)
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
                     "created_by='njit_www_crawl'").fetchone()[0]
    assert n == 2                                        # only first 2 of 5 crawled
    assert out["reconcile"]["skipped_reason"] == "limit_partial_frontier"   # partial → never retire


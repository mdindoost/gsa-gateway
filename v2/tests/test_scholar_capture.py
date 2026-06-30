"""TDD for the Scholar maximal-capture fetcher (2026-06-29 design).

Pure parsers + highlight derivation + peak guard + the 2-fetch/append-history orchestrator.
Trimmed HTML fixtures (not full 200KB pages) modeled on the validated Scholar selectors.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.scholar import (
    parse_scholar_recent, parse_cites_per_year, parse_scholar_publications,
    parse_scholar_coauthors, parse_scholar_profile, derive_highlights,
    all_time_peak, refresh_scholar,
)

# ---- fixtures -------------------------------------------------------------

METRICS_WITH_RECENT = """
<table id="gsc_rsb_st"><tbody>
  <tr><th></th><th class="gsc_rsb_sth">All</th><th class="gsc_rsb_sth">Since 2021</th></tr>
  <tr><td>Citations</td><td>2,791</td><td>1,063</td></tr>
  <tr><td>h-index</td><td>26</td><td>19</td></tr>
  <tr><td>i10-index</td><td>35</td><td>22</td></tr>
</tbody></table>
"""

METRICS_NO_RECENT = """
<table id="gsc_rsb_st"><tbody>
  <tr><th></th><th class="gsc_rsb_sth">All</th></tr>
  <tr><td>Citations</td><td>42</td></tr>
  <tr><td>h-index</td><td>3</td></tr>
  <tr><td>i10-index</td><td>0</td></tr>
</tbody></table>
"""

CHART = """
<div class="gsc_md_hist_b">
  <span class="gsc_g_t">2024</span><span class="gsc_g_t">2025</span><span class="gsc_g_t">2026</span>
  <a class="gsc_g_a"><span class="gsc_g_al">2</span></a>
  <a class="gsc_g_a"><span class="gsc_g_al">15</span></a>
  <a class="gsc_g_a"><span class="gsc_g_al">5</span></a>
</div>
"""


def _pub(title, authors, venue_year, year, cited, cluster):
    cited_cell = f'<a class="gsc_a_ac gs_ibl">{cited}</a>' if cited else '<a class="gsc_a_ac gs_ibl"></a>'
    return (f'<tr class="gsc_a_tr"><td class="gsc_a_t">'
            f'<a class="gsc_a_at" href="/citations?hl=en&user=U&citation_for_view=U:{cluster}">{title}</a>'
            f'<div class="gs_gray">{authors}</div><div class="gs_gray">{venue_year}</div></td>'
            f'<td class="gsc_a_c">{cited_cell}</td>'
            f'<td class="gsc_a_y"><span class="gsc_a_h gsc_a_hc gs_ibl">{year}</span></td></tr>')


# default (citation-ordered) page: high-cited first
PUBS_CITED = ("<table id='gsc_a_t'><tbody id='gsc_a_b'>"
    + _pub("Old Big Hit", "A B", "Journal X, 2010", "2010", "390", "c1")
    + _pub("Mid Paper", "A C", "Conf Y, 2018", "2018", "50", "c2")
    + _pub("This Year Cited", "A D", "Conf Z, 2026", "2026", "7", "c3")
    + "</tbody></table>")

# pubdate (newest-first) page: brings a brand-new 0-cite 2026 paper not on the cited page
PUBS_DATE = ("<table id='gsc_a_t'><tbody id='gsc_a_b'>"
    + _pub("Brand New", "A E", "Preprint, 2026", "2026", "", "c4")
    + _pub("This Year Cited", "A D", "Conf Z, 2026", "2026", "7", "c3")
    + _pub("Mid Paper", "A C", "Conf Y, 2018", "2018", "50", "c2")
    + "</tbody></table>")

COAUTHORS = """
<div id="gsc_rsb_co"><ul class="gsc_rsb_a">
  <li class="gsc_rsb_aa"><span class="gsc_rsb_a_desc"><a href="/citations?user=AA">Gary Miller</a></span>
      <span class="gsc_rsb_a_ext">Carnegie Mellon University</span></li>
  <li class="gsc_rsb_aa"><span class="gsc_rsb_a_desc"><a href="/citations?user=BB">Zhihui Du</a></span>
      <span class="gsc_rsb_a_ext">AMD</span></li>
</ul></div>
"""

PROFILE = """
<div id="gsc_prf_in">David A. Bader</div>
<div class="gsc_prf_il">Distinguished Professor, NJIT</div>
<div id="gsc_prf_ivh"><a href="https://example.edu/~bader">Homepage</a></div>
<img id="gsc_prf_pup-img" src="https://scholar.google.com/photo.jpg">
<div id="gsc_rsb_mnd">
  <div class="gsc_rsb_m_header"><div>Public access</div><a>View all 99 articles</a></div>
  <div class="gsc_rsb_m">
    <div class="gsc_rsb_m_a"><span>16 articles</span></div>
    <div class="gsc_rsb_m_na"><div>0 articles</div></div>
    <div class="gsc_rsb_m_a"><span>available</span></div>
    <div class="gsc_rsb_m_na"><span>not available</span></div>
  </div>
</div>
"""

# stats table missing the h-index row (Scholar drift / partial parse)
METRICS_PARTIAL = """
<table id="gsc_rsb_st"><tbody>
  <tr><th></th><th>All</th></tr>
  <tr><td>Citations</td><td>500</td></tr>
  <tr><td>i10-index</td><td>5</td></tr>
</tbody></table>
"""

BLOCKED = "<html><body>Please show you're not a robot</body></html>"


def _full_page(metrics=METRICS_WITH_RECENT, pubs=PUBS_CITED):
    return f"<html><body>{PROFILE}{metrics}{CHART}{pubs}{COAUTHORS}</body></html>"


# ---- parse_scholar_recent -------------------------------------------------

def test_recent_metrics_parsed():
    assert parse_scholar_recent(METRICS_WITH_RECENT) == {
        "recent_citations": 1063, "recent_h_index": 19,
        "recent_i10_index": 22, "recent_since_year": 2021}


def test_recent_metrics_none_when_no_since_column():
    assert parse_scholar_recent(METRICS_NO_RECENT) is None


def test_recent_metrics_none_on_blocked():
    assert parse_scholar_recent("<html>robot</html>") is None


# ---- parse_cites_per_year -------------------------------------------------

def test_cites_per_year():
    assert parse_cites_per_year(CHART) == {"2024": 2, "2025": 15, "2026": 5}


def test_cites_per_year_empty_when_no_chart():
    assert parse_cites_per_year("<html></html>") == {}


def test_cites_per_year_comma_value_no_year_shift():
    # a 4-digit comma value must parse AND not shift later years onto wrong counts
    chart = ("<div><span class='gsc_g_t'>2014</span><span class='gsc_g_t'>2015</span>"
             "<span class='gsc_g_t'>2016</span>"
             "<a class='gsc_g_a'><span class='gsc_g_al'>900</span></a>"
             "<a class='gsc_g_a'><span class='gsc_g_al'>1,200</span></a>"
             "<a class='gsc_g_a'><span class='gsc_g_al'>800</span></a></div>")
    assert parse_cites_per_year(chart) == {"2014": 900, "2015": 1200, "2016": 800}


# ---- parse_scholar_publications ------------------------------------------

def test_publications_parsed_with_cited_by():
    pubs = parse_scholar_publications(PUBS_CITED)
    assert len(pubs) == 3
    assert pubs[0] == {"title": "Old Big Hit", "authors": "A B", "venue": "Journal X, 2010",
                       "year": "2010", "cited_by": 390,
                       "url": "https://scholar.google.com/citations?hl=en&user=U&citation_for_view=U:c1"}


def test_publications_zero_cite_paper_is_zero():
    pubs = parse_scholar_publications(PUBS_DATE)
    brand_new = [p for p in pubs if p["title"] == "Brand New"][0]
    assert brand_new["cited_by"] == 0 and brand_new["year"] == "2026"


def test_publications_empty_when_none():
    assert parse_scholar_publications("<html></html>") == []


def test_publications_comma_formatted_cited_by():
    html = ("<table><tbody>" + _pub("Big", "A", "J, 2010", "2010", "1,234", "cz")
            + "</tbody></table>")
    assert parse_scholar_publications(html)[0]["cited_by"] == 1234


# ---- derive_highlights ----------------------------------------------------

def test_derive_top_cited_is_most_cited_descending():
    cited = parse_scholar_publications(PUBS_CITED)
    date = parse_scholar_publications(PUBS_DATE)
    hl = derive_highlights(cited, date, today=datetime.date(2026, 6, 29))
    assert [p["cited_by"] for p in hl["top_cited"]] == [390, 50, 7, 0]  # desc, deduped union


def test_derive_newest_includes_brand_new_from_date_page():
    cited = parse_scholar_publications(PUBS_CITED)
    date = parse_scholar_publications(PUBS_DATE)
    hl = derive_highlights(cited, date, today=datetime.date(2026, 6, 29))
    titles = [p["title"] for p in hl["newest"]]
    assert "Brand New" in titles                       # only on the date page; the merge caught it
    assert titles[0:1] == ["Brand New"] or hl["newest"][0]["year"] == "2026"


def test_derive_current_year_filters_and_ranks():
    cited = parse_scholar_publications(PUBS_CITED)
    date = parse_scholar_publications(PUBS_DATE)
    hl = derive_highlights(cited, date, today=datetime.date(2026, 6, 29))
    assert {p["title"] for p in hl["current_year"]} == {"This Year Cited", "Brand New"}
    assert [p["cited_by"] for p in hl["current_year"]] == [7, 0]   # most-cited first


def test_derive_current_year_empty_when_no_papers_this_year():
    cited = parse_scholar_publications(PUBS_CITED)
    hl = derive_highlights(cited, [], today=datetime.date(2030, 1, 1))
    assert hl["current_year"] == []


def test_derive_dedup_across_pages_by_cluster():
    cited = parse_scholar_publications(PUBS_CITED)
    date = parse_scholar_publications(PUBS_DATE)
    hl = derive_highlights(cited, date, today=datetime.date(2026, 6, 29))
    # "Mid Paper" (c2) and "This Year Cited" (c3) appear on BOTH pages — must not duplicate
    all_clusters = [p["url"] for p in hl["top_cited"]]
    assert len(all_clusters) == len(set(all_clusters))


# ---- all_time_peak --------------------------------------------------------

def test_peak_is_all_time_when_peak_exceeds_hidden():
    # chart sums to 2760, citations 2791 -> hidden 31; peak 251 > 31 -> all-time
    chart = {"2007": 8, "2025": 251}  # sum 259
    year, val, all_time = all_time_peak(citations=300, cites_per_year=chart)
    assert (year, val) == ("2025", 251) and all_time is True   # hidden 41 < 251


def test_peak_not_all_time_when_hidden_could_exceed():
    chart = {"2025": 50}                       # sum 50
    year, val, all_time = all_time_peak(citations=1000, cites_per_year=chart)
    assert (year, val) == ("2025", 50) and all_time is False   # hidden 950 > 50


def test_peak_none_when_missing_inputs():
    assert all_time_peak(citations=None, cites_per_year={"2025": 5}) is None
    assert all_time_peak(citations=100, cites_per_year={}) is None


# ---- parse_scholar_coauthors / profile -----------------------------------

def test_coauthors_parsed():
    co = parse_scholar_coauthors(COAUTHORS)
    assert co == [
        {"name": "Gary Miller", "affiliation": "Carnegie Mellon University",
         "url": "https://scholar.google.com/citations?user=AA"},
        {"name": "Zhihui Du", "affiliation": "AMD",
         "url": "https://scholar.google.com/citations?user=BB"}]


def test_coauthors_empty_when_none():
    assert parse_scholar_coauthors("<html></html>") == []


def test_profile_scalars_parsed():
    p = parse_scholar_profile(PROFILE)
    assert p["affiliation"] == "Distinguished Professor, NJIT"
    assert p["homepage"] == "https://example.edu/~bader"
    assert p["photo"] == "https://scholar.google.com/photo.jpg"
    assert p["public_access"] == {"available": 16, "not_available": 0}


def test_profile_public_access_null_when_absent():
    p = parse_scholar_profile('<div id="gsc_prf_in">X</div>')
    assert p["public_access"] is None


def test_profile_public_access_ignores_view_all_header_number():
    # the "View all 99 articles" header number must NOT be mistaken for the available count
    p = parse_scholar_profile(PROFILE)
    assert p["public_access"] == {"available": 16, "not_available": 0}


def test_profile_photo_is_absolutized():
    p = parse_scholar_profile('<img id="gsc_prf_pup-img" src="/citations?view_op=view_photo&user=U">')
    assert p["photo"] == "https://scholar.google.com/citations?view_op=view_photo&user=U"


# ---- orchestrator: refresh_scholar ---------------------------------------

@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','department')")
    c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person','p/k','Koutis',?,'crawler')",
              (json.dumps({"profiles": {"scholar": {"url": "https://scholar.google.com/x"}}}),))
    c.commit()
    yield c
    c.close()


def _two_page_fetch(default_html, date_html):
    def fetch(url):
        return (date_html, "ok") if "sortby=pubdate" in url else (default_html, "ok")
    return fetch


def _sch(conn, key="p/k"):
    return json.loads(conn.execute("SELECT attrs FROM nodes WHERE key=?", (key,)).fetchone()[0]
                      )["profiles"]["scholar"]


def _conn_with(people):
    """Fresh in-memory DB with people in a KNOWN order (for consecutive-block tests)."""
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'CS','cs','department')")
    for k, u in people:
        c.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person',?,?,?,'crawler')",
                  (k, k, json.dumps({"profiles": {"scholar": {"url": u}}})))
    c.commit()
    return c


def _outcome_fetch():
    """A url containing 'BLOCK' returns a CAPTCHA page; otherwise a real profile page."""
    def fetch(url):
        if "BLOCK" in url:
            return (BLOCKED, "ok")
        return (_full_page(pubs=PUBS_DATE), "ok") if "sortby=pubdate" in url else (_full_page(), "ok")
    return fetch


def test_refresh_assembles_full_bag(conn):
    fetch = _two_page_fetch(_full_page(pubs=PUBS_CITED), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-06-29")
    conn.commit()
    sch = _sch(conn)
    assert sch["citations"] == 2791 and sch["recent_citations"] == 1063
    assert sch["cites_per_year"] == {"2024": 2, "2025": 15, "2026": 5}
    assert len(sch["top_cited"]) == 4 and sch["top_cited"][0]["cited_by"] == 390
    assert any(p["title"] == "Brand New" for p in sch["newest"])
    assert len(sch["coauthors"]) == 2
    assert sch["public_access"] == {"available": 16, "not_available": 0}
    assert sch["url"] == "https://scholar.google.com/x"          # preserved


def test_refresh_does_two_fetches_default_and_pubdate(conn):
    seen = []
    def fetch(url):
        seen.append(url)
        return (_full_page(pubs=PUBS_DATE), "ok") if "sortby=pubdate" in url else (_full_page(), "ok")
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-06-29")
    assert len(seen) == 2 and any("sortby=pubdate" in u for u in seen)


def test_history_appends_across_refreshes(conn):
    fetch = _two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-05-01")
    conn.commit()
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-06-01")
    conn.commit()
    hist = _sch(conn)["history"]
    assert [h["date"] for h in hist] == ["2026-05-01", "2026-06-01"]
    assert hist[0]["citations"] == 2791 and hist[0]["h_index"] == 26


def test_history_same_day_dedup(conn):
    fetch = _two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-06-01")
    conn.commit()
    refresh_scholar(conn, fetch=fetch, delay=0, today="2026-06-01")
    conn.commit()
    assert len(_sch(conn)["history"]) == 1            # same date overwrites, not appended


def test_skips_when_pubdate_page_is_blocked_captcha(conn):
    # html2 returns HTTP 200 but is a CAPTCHA/block (no stats table) -> must NOT write empties
    good = _two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=good, delay=0, today="2026-05-01")
    conn.commit()
    before = _sch(conn)
    def blocked2(url):
        return (BLOCKED, "ok") if "sortby=pubdate" in url else (_full_page(), "ok")
    out = refresh_scholar(conn, fetch=blocked2, delay=0, today="2026-06-01")
    conn.commit()
    assert out["failed"] == 1 and out["updated"] == 0
    assert _sch(conn) == before                        # not wiped by a silent block


def test_partial_metrics_dict_counts_failed_not_crash(conn):
    # stats table missing a row -> partial dict; must fail that person, never KeyError-crash the job
    page = f"<html><body>{PROFILE}{METRICS_PARTIAL}{CHART}{PUBS_CITED}{COAUTHORS}</body></html>"
    out = refresh_scholar(conn, fetch=lambda u: (page, "ok"), delay=0, today="2026-06-01")
    conn.commit()
    assert out["failed"] == 1 and out["updated"] == 0
    assert "citations" not in _sch(conn)               # nothing written


def test_jitter_between_people_uses_rand_range(conn):
    conn.execute("INSERT INTO nodes(type,key,name,attrs,source) VALUES('Person','p/k2','Two',?,'crawler')",
                 (json.dumps({"profiles": {"scholar": {"url": "https://scholar.google.com/y"}}}),))
    conn.commit()
    slept, rands = [], []
    fetch = _two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=fetch, jitter=(45, 100), fetch_gap=0,
                    sleep=slept.append, rand=lambda a, b: (rands.append((a, b)), 77.0)[1],
                    today="2026-06-01")
    assert (45, 100) in rands          # jittered between-people delay drawn from the range
    assert 77.0 in slept               # ...and actually slept that long


def test_fetch_gap_sleeps_between_two_fetches(conn):
    slept = []
    refresh_scholar(conn, fetch=_two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE)),
                    delay=0, fetch_gap=4.0, sleep=slept.append, today="2026-06-01")
    assert 4.0 in slept                # the intra-person gap between the 2 fetches


def test_block_abort_stops_after_consecutive_blocks(conn):
    c = _conn_with([("p/b1", "https://s/BLOCK1"), ("p/b2", "https://s/BLOCK2"),
                    ("p/b3", "https://s/BLOCK3")])
    calls = []
    def fetch(u):
        calls.append(u)
        return (BLOCKED, "ok")
    out = refresh_scholar(c, fetch=fetch, delay=0, block_abort=2, sleep=lambda s: None,
                          today="2026-06-01")
    assert out["aborted"] is True and out["failed"] == 2
    assert len(calls) == 2             # stopped — did NOT keep hammering all 3
    c.close()


def test_consecutive_block_counter_resets_on_success(conn):
    # order: BLOCK, success, BLOCK with block_abort=2 -> success resets, so NO abort
    c = _conn_with([("p/b1", "https://s/BLOCK1"), ("p/g", "https://s/good"),
                    ("p/b2", "https://s/BLOCK2")])
    out = refresh_scholar(c, fetch=_outcome_fetch(), delay=0, block_abort=2, sleep=lambda s: None,
                          today="2026-06-01")
    assert out["aborted"] is False and out["updated"] == 1 and out["failed"] == 2
    c.close()


def test_atomic_skip_when_second_fetch_fails_keeps_prior_data(conn):
    # first: a good refresh populates data
    good = _two_page_fetch(_full_page(), _full_page(pubs=PUBS_DATE))
    refresh_scholar(conn, fetch=good, delay=0, today="2026-05-01")
    conn.commit()
    before = _sch(conn)
    # second: pubdate fetch fails -> whole person skipped, no wipe
    def half(url):
        return ("", "error:Timeout") if "sortby=pubdate" in url else (_full_page(), "ok")
    out = refresh_scholar(conn, fetch=half, delay=0, today="2026-06-01")
    conn.commit()
    after = _sch(conn)
    assert out["failed"] == 1 and out["updated"] == 0
    assert after == before                             # untouched: history still len 1, lists intact

import json, os, re
from facultyfolio import db, render, config


def _funded_slugs():
    c = db.connect()
    rows = c.execute("SELECT key, attrs FROM nodes WHERE type='Person' AND is_active=1 "
                     "AND json_extract(attrs,'$.funding') IS NOT NULL").fetchall()
    c.close()
    return [(r["key"].split("/")[-1], json.loads(r["attrs"])) for r in rows]


def test_njit_total_equals_sum_of_contributing_rows():
    for slug, attrs in _funded_slugs():
        f = attrs.get("funding", {})
        if "nsf" in f:
            s = sum(a["obligated"] for a in f["nsf"]["awards"] if a.get("at_njit"))
            assert s == f["nsf"]["njit_total"], f"NSF drift for {slug}"
        if "nih" in f:
            s = sum(p["total"] for p in f["nih"]["projects"] if p.get("role") == "contact")
            assert s == f["nih"]["njit_total"], f"NIH drift for {slug}"


def test_no_funding_on_hero():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    hero = html.split('id="funding"')[0]        # everything before the funding section
    assert "$" not in hero
    assert "fund-" not in hero
    assert "rollup" not in hero


def test_profile_funding_section_no_dollars_no_copi():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    start = html.index('id="funding"')
    sec = html[start:html.index("</section>", start)]
    scaffold = re.sub(r'class="fund-t"[^>]*>.*?<', "<", sec, flags=re.S)   # drop verbatim titles
    assert "$" not in scaffold
    assert "co-PI" not in sec and "fund-cite" not in sec


AGGREGATE_PAGES = ["ywcc/computer-science/index.html", "ywcc/index.html", "index.html"]


def test_aggregate_pages_no_dollars_no_fund_classes():
    for rel in AGGREGATE_PAGES:
        out = os.path.join(config.OUT_ROOT, rel)
        assert os.path.exists(out), f"build the site first: {rel}"
        html = open(out).read()
        assert "fund-" not in html          # profile-only classes never leak here
        assert "$" not in html              # no dollars anywhere on aggregate pages

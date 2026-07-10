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


def test_no_dollars_or_fund_classes_on_person_card_or_hero():
    # person-card macro (leaderboard/hub rows) and the profile hero must carry no funding.
    f = db.get_faculty("zhiwei")            # a funded person
    html = render.render_profile(f)
    # the hero/aside is the left identity card; funding lives only in the #funding section.
    hero = html.split('id="funding"')[0]    # everything before the funding section
    assert "$" not in hero
    assert "fund-" not in hero
    assert "rollup" not in hero


# Every aggregate page: no per-person .fund- classes leak, and every '$' sits inside a .rollup.
AGGREGATE_PAGES = [
    "ywcc/computer-science/index.html",     # dept leaderboard
    "ywcc/index.html",                      # college hub
    "index.html",                           # NJIT root hub
]


def test_aggregate_pages_no_fund_classes_and_dollars_only_in_rollup():
    for rel in AGGREGATE_PAGES:
        out = os.path.join(config.OUT_ROOT, rel)
        assert os.path.exists(out), f"build the site first (Task 6/8): {rel}"
        html = open(out).read()
        assert "fund-" not in html, f"profile-only .fund- class leaked into {rel}"
        non_rollup = re.sub(r'<div class="rollup".*?</div>', "", html, flags=re.S)
        assert "$" not in non_rollup, f"a '$' appears outside the aggregate rollup in {rel}"

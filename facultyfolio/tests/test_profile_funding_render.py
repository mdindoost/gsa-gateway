import re
from facultyfolio import db, render


def _funding_section(html):
    # the #funding <section> ... first </section> after it
    start = html.index('id="funding"')
    return html[start:html.index("</section>", start)]


def test_funded_profile_has_funding_section_no_dollars():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    assert 'class="eyebrow">Sponsored research' in html
    assert "Research funding" in html
    sec = _funding_section(html)
    assert "as Principal Investigator" in sec or "as Contact PI" in sec
    assert "reporter.nih.gov/project-details/" in sec        # NIH links resolve (appl_id live)
    # no dollars in the scaffolding (strip the verbatim <a/span class="fund-t"> titles first)
    scaffold = re.sub(r'class="fund-t"[^>]*>.*?<', "<", sec, flags=re.S)
    assert "$" not in scaffold
    assert "fund-cite" not in sec                            # dollar cell removed
    assert "co-PI" not in sec                                # co-PI chip gone


def test_unfunded_profile_has_no_funding_section():
    f = dict(db.get_faculty("zhiwei")); f["funding"] = {}
    assert "Research funding" not in render.render_profile(f)


def test_funded_but_no_scholar_still_renders_funding():
    f = db.get_faculty("calvin")
    if not f.get("funding"):
        import pytest; pytest.skip("fixture slug not funded; pick another no-Scholar funded slug")
    f = dict(f); f["scholar"] = None
    assert "Research funding" in render.render_profile(f)

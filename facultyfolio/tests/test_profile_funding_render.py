from facultyfolio import db, render


def test_funded_profile_has_funding_section():
    f = db.get_faculty("zhiwei")
    html = render.render_profile(f)
    assert 'class="eyebrow">Sponsored research' in html
    assert "Research funding" in html
    assert "obligated" in html and "costs" in html
    assert "reporter.nih.gov/project-details/" in html


def test_unfunded_profile_has_no_funding_section():
    # find a person with no funding; Selected work etc. still render
    f = db.get_faculty("zhiwei")
    f = dict(f); f["funding"] = {}
    html = render.render_profile(f)
    assert "Research funding" not in html


def test_funded_but_no_scholar_still_renders_funding():
    # B2 guard: funding must render even when the Scholar/pubs block is absent.
    f = db.get_faculty("calvin")         # funded, no Scholar block (verify slug at build time)
    if not f.get("funding"):
        import pytest; pytest.skip("fixture slug not funded; pick another no-Scholar funded slug")
    f = dict(f); f["scholar"] = None
    html = render.render_profile(f)
    assert "Research funding" in html

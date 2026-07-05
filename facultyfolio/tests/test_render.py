import copy
from facultyfolio import render, db


def _koutis():
    return db.get_faculty(33)


def test_profile_koutis_sections():
    html = render.render_profile(_koutis())
    # "&" is HTML-escaped by autoescape, matching the reference's "Impact &amp; trajectory"
    for s in ("Areas of focus", "Background", "Impact &amp; trajectory", "Selected work", "Awards &amp; honors"):
        assert s in html
    assert "Ioannis Koutis" in html and "Koutis, Ioannis" not in html
    assert "Active since" in html and ">2007<" in html and "Dept. rank" not in html   # rank cut, "Active since 2007"
    assert 'class="bar peak"' in html                                    # chart present
    assert "not written or generated" in html                           # provenance label
    assert "2,791" in html                                              # comma-formatted citations
    from facultyfolio import config
    assert config.ASSISTANT_VERSION in html   # footer tracks identity source, not a re-hardcoded string


def test_profile_junior_no_office_row():
    f = db.get_faculty("km982")            # Kieran — no office/phone, joint appointment
    html = render.render_profile(f)
    assert "Joint appointment" in html
    assert "<span class=\"about-k\">Office</span>" not in html          # office row omitted
    assert "Active since" in html


def test_profile_teaching_interests_row():
    # Houle's KG teaching string carries a "Teaching Interests;" section that must
    # render as its own row, not be dropped in favour of the single course.
    html = render.render_profile(db.get_faculty("meh43"))
    assert "<span class=\"about-k\">Teaching interests</span>" in html
    assert "Data Structures &amp; Algorithms" in html          # was previously dropped
    assert "Data Mining" in html


def test_profile_degraded_education_omits_row():
    f = db.get_faculty("oria")             # education == "Ph.D." only
    html = render.render_profile(f)
    assert "<span class=\"about-k\">Education</span>" not in html


def test_missing_scholar_single_hook():
    f = _koutis()
    f["scholar"] = None
    html = render.render_profile(f)
    assert html.count('class="hook"') == 2          # missing-scholar + Recognition (positive)
    assert "No Google Scholar profile" in html
    assert "Selected work" not in html              # publications folded away


def test_worst_case_no_scholar_no_areas():
    f = _koutis()
    f["scholar"] = None
    f["areas"] = []
    html = render.render_profile(f)
    assert html.count('class="hook"') == 3          # research + scholar + recognition, within budget


def test_monogram_when_no_photo():
    f = _koutis()
    html = render.render_profile(f, photo_ref="monogram:IK")
    assert ">IK<" in html and "<svg class=\"photo\"" in html


def test_no_llm_prose_leaks():
    # even if an 'about' bio were somehow attached, render only reads the crawler fields
    html = render.render_profile(_koutis())
    assert "not written or generated" in html


def test_leaderboard():
    from facultyfolio import rank, config
    lst = rank.ranked_list(config.CS_ORG_ID)
    cov = rank.coverage(config.CS_ORG_ID)
    html = render.render_leaderboard("Computer Science", lst, cov)
    assert "39 of 57" in html and "faculty with Google Scholar data" in html
    assert "by total citations" in html
    assert "../p/" in html                       # rows link to profiles
    assert html.count('class="lb-row"') == 39
    assert config.ASSISTANT_VERSION in html      # footer version reaches the leaderboard too

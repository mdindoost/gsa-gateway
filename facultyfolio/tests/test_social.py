"""Task 1 — social-icons Fixed/Adaptive modes (render-side, pure)."""
from facultyfolio import render

_ORDER = ["email", "scholar", "website", "linkedin", "github", "orcid"]


def _f(**kw):
    base = {"name": "Test Person", "email": None, "profiles": {}}
    base.update(kw)
    return base


def test_fixed_shows_all_six_in_order():
    icons = render.social_icons(_f(email="x@njit.edu",
                                   profiles={"scholar": {"url": "s"}}), "Fixed")
    assert [i["key"] for i in icons] == _ORDER          # every type, fixed order
    active = {i["key"] for i in icons if i["active"]}
    assert active == {"email", "scholar"}                # present ones active
    assert not any(i["active"] for i in icons if i["key"] in ("website", "linkedin", "github", "orcid"))


def test_adaptive_shows_only_present():
    icons = render.social_icons(_f(email="x@njit.edu",
                                   profiles={"linkedin": {"url": "l"}}), "Adaptive")
    assert [i["key"] for i in icons] == ["email", "linkedin"]
    assert all(i["active"] for i in icons)


def test_website_equal_scholar_deduped_both_modes():
    # Houle case: website url IS the scholar url -> website is not a distinct link
    prof = {"scholar": {"url": "http://sch"}, "website": {"url": "http://sch"}}
    fixed = render.social_icons(_f(profiles=prof), "Fixed")
    web = next(i for i in fixed if i["key"] == "website")
    assert web["active"] is False                        # duplicate -> grayed in Fixed
    adaptive = render.social_icons(_f(profiles=prof), "Adaptive")
    assert "website" not in [i["key"] for i in adaptive]  # duplicate -> omitted in Adaptive


def test_website_equal_any_profile_deduped():
    # broader dedup: website URL equal to a DIFFERENT profile's url is still not distinct
    prof = {"linkedin": {"url": "http://dup"}, "website": {"url": "http://dup"}}
    icons = {i["key"]: i["active"] for i in render.social_icons(_f(profiles=prof), "Fixed")}
    assert sum(1 for k in ("website", "linkedin") if icons[k]) == 1   # exactly one stays active


def test_email_missing_grayed_in_fixed():
    icons = {i["key"]: i["active"] for i in render.social_icons(_f(email=None), "Fixed")}
    assert icons["email"] is False                        # no email -> grayed, not omitted


def test_profile_template_renders_gray_span_for_missing():
    html = render.render_profile(_f(email="x@njit.edu", profiles={"scholar": {"url": "s"}},
                                    home_dept="Computer Science", title="Prof"))
    assert 'class="off"' in html                          # missing icons rendered as gray spans
    assert 'title="ORCID' in html                         # ORCID icon present even with no data
    # a grayed icon must NOT be a link — the <span class="off"> carries no href
    import re
    for span in re.findall(r'<span class="off"[^>]*>', html):
        assert "href" not in span

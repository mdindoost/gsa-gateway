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
    from facultyfolio import format as F
    assert F.commafy(_koutis()["scholar"]["citations"]) in html         # comma-formatted citations (self-relative)
    from facultyfolio import config
    assert config.ASSISTANT_VERSION in html   # footer tracks identity source, not a re-hardcoded string


def test_about_rows_fixed_shows_all_with_not_listed():
    f = {"education_raw": "", "teaching_raw": ""}     # nothing present
    rows = render.about_rows(f, "Fixed")
    # "Teaching interests" is ALWAYS adaptive (sparse) -> omitted when empty even in Fixed mode
    assert [r["label"] for r in rows] == ["Education", "Office", "Teaching"]
    assert all(r["present"] is False and r["value"] == "Not listed" for r in rows)


def test_teaching_interests_always_adaptive():
    # present -> shows in Fixed; absent -> omitted in Fixed (unlike Education which grays)
    present = render.about_rows({"education_raw": "", "teaching_raw": "Teaching Interests; AI, ML"}, "Fixed")
    assert any(r["label"] == "Teaching interests" and r["present"] for r in present)
    absent = render.about_rows({"education_raw": "", "teaching_raw": ""}, "Fixed")
    assert "Teaching interests" not in [r["label"] for r in absent]


def test_research_interests_row_after_education():
    # present -> a "Research interests" row shows immediately after Education
    f = {"education_raw": "Education of X (CS): Ph.D.; MIT; CS; 2010",
         "research_statement_raw": "Research statement of X (CS): Research Interests robotics, vision",
         "teaching_raw": ""}
    labels = [r["label"] for r in render.about_rows(f, "Fixed")]
    assert "Research interests" in labels
    assert labels.index("Research interests") == labels.index("Education") + 1
    ri = next(r for r in render.about_rows(f, "Fixed") if r["label"] == "Research interests")
    assert ri["value"] == "robotics, vision" and ri["present"]


def test_research_interests_always_adaptive_omitted_when_empty():
    # no statement (or a patents-only label-less blob) -> row omitted even in Fixed mode
    for rs in ("", "Research statement of X (CS): Patents A Method For Something"):
        f = {"education_raw": "", "research_statement_raw": rs, "teaching_raw": ""}
        assert "Research interests" not in [r["label"] for r in render.about_rows(f, "Fixed")]


def test_about_rows_adaptive_omits_empty():
    f = {"education_raw": "", "teaching_raw": ""}
    assert render.about_rows(f, "Adaptive") == []    # nothing present -> no rows


def test_about_rows_adaptive_partial_mix():
    # Adaptive: present rows kept, empty ones dropped (no "Not listed" placeholder)
    f = {"education_raw": "", "teaching_raw": "Past Courses; CS 675: MACHINE LEARNING"}
    rows = render.about_rows(f, "Adaptive")
    assert [r["label"] for r in rows] == ["Teaching"]
    assert rows[0]["present"] is True and rows[0]["value"] == "Machine Learning"
    assert all(r["value"] != "Not listed" for r in rows)


def test_profile_no_office_shows_not_listed_fixed():
    # Fixed default: Kieran has no office -> the Office row is PRESENT but grayed "Not listed"
    html = render.render_profile(db.get_faculty("km982"))
    assert "Joint appointment" in html
    assert "<span class=\"about-k\">Office</span>" in html              # row now shown
    assert "about-row off" in html and "Not listed" in html            # grayed placeholder
    assert "Active since" in html


def test_appointment_includes_affiliated():
    f = {"name": "Guiling Wang", "title": "Distinguished Professor",
         "home_dept": "Computer Science", "joint_dept": "Data Science",
         "affiliated_depts": ["Martin Tuchman School of Management (MTSM)"],
         "college": "Ying Wu College of Computing"}
    html = render.render_profile(f)
    assert "joint appointment in Data Science" in html
    assert "affiliated with Martin Tuchman School of Management (MTSM)" in html


def test_appointment_no_affiliated_unchanged():
    # a single-home person (Koutis) renders with no dangling "affiliated"
    html = render.render_profile(db.get_faculty(33))
    assert "affiliated with" not in html


def test_profile_teaching_interests_row():
    # Houle's KG teaching string carries a "Teaching Interests;" section that must
    # render as its own row, not be dropped in favour of the single course.
    html = render.render_profile(db.get_faculty("meh43"))
    assert "<span class=\"about-k\">Teaching interests</span>" in html
    assert "Data Structures &amp; Algorithms" in html          # was previously dropped
    assert "Data Mining" in html


def test_profile_degraded_education_not_listed_fixed():
    # Fixed default: a faculty with no parseable education -> Education row grays to "Not listed"
    f = _koutis()
    f["education_raw"] = ""
    html = render.render_profile(f)
    assert "<span class=\"about-k\">Education</span>" in html
    assert "Not listed" in html


def test_oria_per_degree_education_renders():
    # regression: Oria's per-degree layout now renders both degrees (was blank before the fix)
    html = render.render_profile(db.get_faculty("oria"))
    assert "Diplôme" in html and "(1989)" in html          # apostrophe is HTML-escaped, avoid it
    assert "École Nationale Supérieure des Télécommunications" in html and "(1994)" in html


def test_profile_sources_label_not_cs_specific():
    html = render.render_profile(db.get_faculty(33))            # Koutis has Scholar
    assert "Scholar + NJIT" in html and "NJIT-CS" not in html


def test_profile_back_link_uses_home_segment():
    html = render.render_profile(db.get_faculty(33))            # home = Computer Science
    assert '../computer-science/index.html' in html
    assert '../cs/index.html' not in html


def test_profile_mx6_njit_research_interest_and_scholar_chips_coexist():
    # the whole point: her NJIT prose shows in Background AND her Scholar chips stay in Areas of focus
    html = render.render_profile(db.get_faculty("mx6"))
    assert "Research interests" in html
    assert "Machine learning theory" in html and "graph representation learning" in html
    assert "Graph Machine Learning" in html and "LLMs" in html      # union chips untouched


def test_research_interest_html_escaped():
    f = _koutis()
    f["research_statement_raw"] = ("Research statement of K (CS): Research Interests "
                                   "<script>alert(1)</script> robotics")
    html = render.render_profile(f)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_missing_scholar_single_hook():
    f = _koutis()
    f["scholar"] = None
    f["awards_raw"], f["service_raw"] = [], ""       # no recognition data -> positive hook shows
    html = render.render_profile(f)
    assert html.count('class="hook"') == 2          # missing-scholar + Recognition (positive)
    assert "No Google Scholar profile" in html
    assert "Selected work" not in html              # publications folded away


def test_worst_case_no_scholar_no_areas():
    f = _koutis()
    f["scholar"] = None
    f["areas"] = []
    f["awards_raw"], f["service_raw"] = [], ""       # nothing anywhere -> the true worst case
    html = render.render_profile(f)
    assert html.count('class="hook"') == 3          # research + scholar + recognition, within budget


def test_recognition_renders_awards_and_service():
    f = _koutis()
    f["awards_raw"] = ["2022 Best Paper Award, ACM", "2019"]     # bare-year row must be dropped
    f["service_raw"] = "Service by Ioannis Koutis (Computer Science): Program Committee, 2022"
    html = render.render_profile(f)
    assert '<ul class="awards">' in html
    assert "2022 Best Paper Award, ACM" in html
    assert html.count("<li>") >= 1 and ">2019<" not in html      # noise row de-noised
    assert "Professional service" in html and "Program Committee, 2022" in html
    # awards present -> the positive Recognition hook is gone (that section shows data now)
    assert "Awards aren't in our public data" not in html


def test_recognition_hook_when_empty():
    f = _koutis()
    f["awards_raw"], f["service_raw"] = [], ""
    html = render.render_profile(f)
    assert "Awards aren't in our public data" in html            # honest positive hook


def test_recognition_service_only():
    f = _koutis()
    f["awards_raw"] = []                                          # no awards, service present
    f["service_raw"] = "Service by Ioannis Koutis (Computer Science): Reviewer, NSF, 2022"
    html = render.render_profile(f)
    assert '<ul class="awards">' not in html                     # no awards list
    assert "Professional service" in html and "Reviewer, NSF, 2022" in html
    assert "Awards aren't in our public data" not in html        # data present -> no hook


def test_recognition_escapes_hostile_award():
    f = _koutis()
    f["awards_raw"] = ["2020 <script>alert(1)</script> Award", "2019 Plain Award"]
    f["service_raw"] = ""
    html = render.render_profile(f)
    assert "<script>alert(1)</script>" not in html               # autoescaped
    assert "&lt;script&gt;" in html


def test_render_hub_cards_and_counts():
    cards = [
        {"name": "Computer Science", "faculty": 57, "scholar": 34, "url": "computer-science/index.html"},
        {"name": "Data Science", "faculty": 21, "scholar": 15, "url": "data-science/index.html"},
    ]
    html = render.render_hub("Ying Wu College of Computing", cards)
    assert "Ying Wu College of Computing" in html
    assert 'href="computer-science/index.html"' in html
    assert ">57<" in html and "on Google Scholar" in html
    assert 'href="assets/style.css"' in html          # root page -> no ../
    from facultyfolio import config
    assert config.ASSISTANT_VERSION in html            # shared footer


def test_render_hub_escapes_hostile_card():
    cards = [{"name": 'X <script>alert(1)</script>', "faculty": 1, "scholar": 0, "url": "x/index.html"}]
    html = render.render_hub("C & <b>", cards)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_monogram_when_no_photo():
    f = _koutis()
    html = render.render_profile(f, photo_ref="monogram:IK")
    assert ">IK<" in html and "<svg class=\"photo\"" in html


def test_no_llm_prose_leaks():
    # even if an 'about' bio were somehow attached, render only reads the crawler fields
    html = render.render_profile(_koutis())
    assert "not written or generated" in html


def _leaderboard_html():
    from facultyfolio import rank, config
    roster = rank.roster(config.CS_ORG_ID)
    cov = rank.coverage(config.CS_ORG_ID)
    views = {"rank": rank.by_rank(roster), "citations": rank.by_citations(roster),
             "az": rank.by_name(roster)}
    stats = rank.leaderboard_stats(roster, cov)
    photo_map = {r["slug"]: f"monogram:{r['name'][:1]}" for r in roster}
    return render.render_leaderboard("Computer Science", views, stats, cov, photo_map), roster


def test_leaderboard_three_panels_default_rank():
    from facultyfolio import config
    html, _ = _leaderboard_html()
    for v in ("rank", "citations", "az"):
        assert f'data-view="{v}"' in html
    # rank panel is the default-visible one; the other two panels are hidden
    assert '<div class="lb-panel" data-view="rank">' in html          # no ' hidden' on rank
    assert '<div class="lb-panel" data-view="citations" hidden>' in html
    assert '<div class="lb-panel" data-view="az" hidden>' in html
    assert config.ASSISTANT_VERSION in html


def test_leaderboard_controls_and_stats():
    html, _ = _leaderboard_html()
    assert 'class="lb-switch"' in html and 'class="lb-search"' in html
    assert html.count('<button type="button" data-view=') == 3        # 3 view buttons
    assert 'class="lb-glance"' in html and "on Google Scholar" in html


def test_leaderboard_all_faculty_every_panel():
    html, roster = _leaderboard_html()
    slugs = {r["slug"] for r in roster}
    assert "acz6" in slugs and "ikoutis" in slugs                      # both are known CS faculty
    # every faculty links from all THREE panels -> exactly 3 hrefs per slug (unconditional)
    for slug in ("acz6", "ikoutis"):
        assert html.count(f'../p/{slug}.html') == 3


def test_leaderboard_chair_group_first_and_no_scholar_grayed():
    html, roster = _leaderboard_html()
    rank_panel = html.split('<div class="lb-panel" data-view="rank">', 1)[1] \
                     .split('<div class="lb-panel" data-view="citations"', 1)[0]
    first_group = rank_panel.split('lb-group-h">', 1)[1].split('<', 1)[0]
    assert first_group == "Department Chair"                           # Chair heads the rank view
    # Zaidenberg (acz6) has no Scholar -> grayed + em-dash in the citations panel (unconditional)
    assert not any(r["slug"] == "acz6" and r["citations"] is not None for r in roster)
    cite_panel = html.split('<div class="lb-panel" data-view="citations"', 1)[1] \
                     .split('<div class="lb-panel" data-view="az"', 1)[0]
    before_acz6 = cite_panel.split('../p/acz6.html', 1)[0]
    assert 'no-scholar' in before_acz6.rsplit('<a class="lb-row', 1)[1]   # her row carries the grayed class


def test_hidden_panel_css_guard():
    # regression: the view panels use a class+attr display rule (specificity 0,2,0) that
    # outranks the UA [hidden]{display:none} — without an explicit [hidden] override the
    # non-default panels stay visible and stack. Guard that the override survives.
    import os
    css = open(os.path.join(os.path.dirname(render.__file__), "assets", "style.css"), encoding="utf-8").read()
    assert ".lb-panel[hidden]" in css
    after = css.split(".lb-panel[hidden]", 1)[1].split("}", 1)[0]
    assert "display:none" in after and "!important" in after


def test_leaderboard_escapes_hostile_characters():
    from facultyfolio import rank
    hostile = [{"slug": "x1", "name": 'A "Quote" & <b>Bold</b>', "title": 'Prof <script>',
                "rank_index": 2, "rank_label": "Professor", "citations": None, "h_index": None,
                "areas": ['R&D <tag>']}]
    views = {"rank": rank.by_rank(hostile), "citations": rank.by_citations(hostile),
             "az": rank.by_name(hostile)}
    stats = rank.leaderboard_stats(hostile, (0, 1))
    html = render.render_leaderboard("CS", views, stats, (0, 1), {"x1": "monogram:AQ"})
    # the hostile PAYLOAD never appears raw (the page's own <script> block is unrelated)
    assert "Prof <script>" not in html and "<b>Bold</b>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html                 # escaped in visible text
    assert 'data-name="a &#34;quote&#34; &amp; &lt;b&gt;bold&lt;/b&gt;"' in html  # escaped in data-* attr too

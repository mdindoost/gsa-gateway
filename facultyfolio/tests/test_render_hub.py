from facultyfolio import render

CARDS = [{"name": "Computer Science", "faculty": 57, "scholar": 39, "url": "computer-science/index.html"}]


def test_hub_renders_title_eyebrow_and_canonical():
    html = render.render_hub("New Jersey Institute of Technology", CARDS,
                             eyebrow="University", asset_root="",
                             canonical="https://facultyfolio.github.io/")
    assert "New Jersey Institute of Technology" in html
    assert "University" in html
    assert '<link rel="canonical" href="https://facultyfolio.github.io/">' in html
    assert 'href="assets/style.css"' in html          # asset_root="" at site root

def test_college_hub_uses_parent_asset_root():
    html = render.render_hub("Ying Wu College of Computing", CARDS,
                             eyebrow="College", asset_root="../",
                             canonical="https://facultyfolio.github.io/ywcc/")
    assert 'href="../assets/style.css"' in html
    assert "computer-science/index.html" in html


def _row(slug, name, title, areas=()):
    return render._lb_row(
        {"slug": slug, "name": name, "title": title, "areas": list(areas),
         "citations": None, "h_index": None, "rank_num": None}, {})


def test_hub_renders_stats_departments_then_leadership_in_order():
    stats = {"total": 119, "with_scholar": 76,
             "groups": [("Department Chair", 3), ("Professor", 13)]}
    leadership = {
        "dean": [_row("js2852", "Jamie Payton", "Dean, Ying Wu College of Computing")],
        "assoc_deans": [_row("bader", "David Bader", "Associate Dean")],
        "chairs": [_row("oria", "Vincent Oria", "Department Chair, Computer Science", ["Databases"])],
    }
    html = render.render_hub("Ying Wu College of Computing", CARDS, eyebrow="College",
                             asset_root="../", stats=stats, leadership=leadership)
    assert "119" in html and "3 · Department Chair" in html
    assert html.index("computer-science/index.html") < html.index("Jamie Payton")
    assert 'href="../p/bader.html"' in html and "David Bader" in html
    assert "Department Chair, Computer Science" in html
    assert '<span class="chip">Databases</span>' in html


def test_hub_without_stats_or_leadership_is_unchanged():
    html = render.render_hub("New Jersey Institute of Technology", CARDS,
                             eyebrow="University", asset_root="")
    assert "lb-glance" not in html
    assert "lb-group" not in html          # no wrapper/header — NJIT hub bare cards, unchanged
    assert '<div class="hub-cards">' in html

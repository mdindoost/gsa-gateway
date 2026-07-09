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

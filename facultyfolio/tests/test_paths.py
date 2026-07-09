import os
from facultyfolio import paths, config

OUT = "/tmp/ffout"

def test_profile_path_unchanged():
    assert paths.profile_path(OUT, "koutis") == os.path.join(OUT, "p", "koutis.html")

def test_leaderboard_path_is_nested_under_college():
    assert paths.leaderboard_path(OUT, "ywcc", "computer-science") == \
        os.path.join(OUT, "ywcc", "computer-science", "index.html")

def test_college_and_njit_hub_paths():
    assert paths.college_hub_path(OUT, "ywcc") == os.path.join(OUT, "ywcc", "index.html")
    assert paths.njit_hub_path(OUT) == os.path.join(OUT, "index.html")

def test_sitemap_and_robots_paths():
    assert paths.sitemap_path(OUT) == os.path.join(OUT, "sitemap.xml")
    assert paths.robots_path(OUT) == os.path.join(OUT, "robots.txt")

def test_rel_root_by_depth():
    assert paths.rel_root(0) == ""
    assert paths.rel_root(1) == "../"
    assert paths.rel_root(2) == "../../"

def test_canonical_url_is_absolute():
    assert paths.canonical_url("p/koutis.html") == config.SITE_ORIGIN + "/p/koutis.html"
    assert paths.canonical_url("ywcc/computer-science/") == \
        config.SITE_ORIGIN + "/ywcc/computer-science/"
    assert paths.canonical_url("") == config.SITE_ORIGIN + "/"

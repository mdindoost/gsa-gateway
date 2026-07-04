import os
from facultyfolio import assets


def test_copy_assets(tmp_path):
    assets.copy_assets(str(tmp_path))
    css_path = os.path.join(tmp_path, "assets", "style.css")
    assert os.path.exists(css_path)
    css = open(css_path).read()
    assert ":root{" in css
    assert "@font-face" in css
    fonts = os.listdir(os.path.join(tmp_path, "assets", "fonts"))
    assert any("fraunces" in f for f in fonts)
    assert any("inter" in f for f in fonts)
    assert any("ibm-plex-mono" in f for f in fonts)
    assert all(f.endswith(".woff2") for f in fonts)

from facultyfolio import seo, config

def test_sitemap_lists_absolute_urls():
    xml = seo.sitemap_xml([config.SITE_ORIGIN + "/", config.SITE_ORIGIN + "/p/koutis.html"])
    assert xml.startswith("<?xml")
    assert "<urlset" in xml
    assert f"<loc>{config.SITE_ORIGIN}/</loc>" in xml
    assert f"<loc>{config.SITE_ORIGIN}/p/koutis.html</loc>" in xml

def test_robots_allows_all_and_points_to_sitemap():
    txt = seo.robots_txt()
    assert "User-agent: *" in txt
    assert "Allow: /" in txt
    assert f"Sitemap: {config.SITE_ORIGIN}/sitemap.xml" in txt

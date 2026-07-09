from facultyfolio import config


def test_published_colleges_is_ordered_list_with_ywcc():
    assert isinstance(config.PUBLISHED_COLLEGES, list)
    assert config.PUBLISHED_COLLEGES == ["ywcc"]


def test_site_origin_is_absolute_no_trailing_slash():
    assert config.SITE_ORIGIN.startswith("https://")
    assert not config.SITE_ORIGIN.endswith("/")


def test_legacy_redirects_target_ywcc_nested_segments_no_leading_slash():
    r = config.LEGACY_REDIRECTS
    assert r["computer-science"] == "ywcc/computer-science"
    assert r["data-science"] == "ywcc/data-science"
    assert r["informatics"] == "ywcc/informatics"
    assert r["cs"] == "ywcc/computer-science"
    for target in r.values():
        assert not target.startswith("/") and "://" not in target

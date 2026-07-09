import os, tempfile
import pytest
from facultyfolio import build

@pytest.fixture(autouse=True)
def _stub_photos(monkeypatch):
    """Every build-touching test in this file stubs photo resolution — else it hits the
    network for all 119 faculty (slow + flaky). Monograms are byte-stable."""
    monkeypatch.setattr(build, "photos_ensure",
                        lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")

def test_full_build_writes_njit_hub_college_hub_and_nested_dept():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        assert os.path.exists(os.path.join(out, "index.html"))                       # NJIT hub
        assert os.path.exists(os.path.join(out, "ywcc", "index.html"))               # college hub
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "p", "ikoutis.html"))                # profile flat
        assert os.path.exists(os.path.join(out, "sitemap.xml"))
        assert os.path.exists(os.path.join(out, "robots.txt"))
        # legacy redirect written at a now-free root segment
        assert os.path.exists(os.path.join(out, "computer-science", "index.html"))

def test_scoped_dept_build_writes_ancestors_not_siblings():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)          # full first
        sib = os.path.join(out, "ywcc", "data-science", "index.html")
        with open(sib, "w") as fh: fh.write("SENTINEL")     # tamper a sibling
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "index.html"))         # ancestor refreshed
        with open(sib) as fh:
            assert fh.read() == "SENTINEL"                  # sibling untouched

def test_parse_scope_from_args():
    from facultyfolio import build as _b
    assert _b._scope_from_args([]) is None
    assert _b._scope_from_args(["--college", "ywcc"]) == {"college": "ywcc"}
    assert _b._scope_from_args(["--dept", "computer-science"]) == {"dept": "computer-science"}


import re
from facultyfolio import db, config

def _read(p):
    with open(p) as fh: return fh.read()

def test_manifest_of_scoped_dept_build():
    """A --dept build writes exactly: CS profiles + CS leaderboard + ancestors + SEO. No DS/Info pages."""
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        assert os.path.exists(os.path.join(out, "ywcc", "computer-science", "index.html"))
        assert os.path.exists(os.path.join(out, "index.html"))
        assert os.path.exists(os.path.join(out, "ywcc", "index.html"))
        assert not os.path.exists(os.path.join(out, "ywcc", "data-science", "index.html"))
        assert not os.path.exists(os.path.join(out, "ywcc", "informatics", "index.html"))

def test_njit_hub_count_matches_college_coverage():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        html = _read(os.path.join(out, "index.html"))
        _, m = db.college_coverage(db.org_node_by_slug("ywcc"))
        assert re.search(rf"<strong>{m}</strong>\s*faculty", html)

def test_scoped_sitemap_still_lists_out_of_scope_depts():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        sm = _read(os.path.join(out, "sitemap.xml"))
        assert f"{config.SITE_ORIGIN}/ywcc/data-science/" in sm       # out of build scope, in sitemap
        assert f"{config.SITE_ORIGIN}/ywcc/informatics/" in sm

def test_all_urls_absolute_in_sitemap():
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)
        sm = _read(os.path.join(out, "sitemap.xml"))
        for loc in re.findall(r"<loc>(.*?)</loc>", sm):
            assert loc.startswith(config.SITE_ORIGIN + "/")

def test_full_build_is_byte_stable():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        build.build_site(scope=None, out_root=a)
        build.build_site(scope=None, out_root=b)
        fa = _read(os.path.join(a, "ywcc", "computer-science", "index.html"))
        fb = _read(os.path.join(b, "ywcc", "computer-science", "index.html"))
        assert fa == fb

def test_scoped_dept_build_leaves_sibling_profile_untouched():
    """A --dept computer-science build must not overwrite a sibling dept's profile page in
    the shared flat /p/ namespace."""
    with tempfile.TemporaryDirectory() as out:
        build.build_site(scope=None, out_root=out)          # full build first
        cs_slugs = set(db.faculty_slugs(config.CS_ORG_ID))
        ds_depts = db.dept_orgs_of_college(db.org_node_by_slug("ywcc"))
        ds_org = next(d for d in ds_depts if d["slug"] == "data-science")
        ds_slugs = db.faculty_slugs(ds_org["node_id"])
        sib_slug = next(s for s in ds_slugs if s not in cs_slugs)  # avoid dup-home edge case
        sib = os.path.join(out, "p", f"{sib_slug}.html")
        with open(sib, "w") as fh: fh.write("SENTINEL")
        build.build_site(scope={"dept": "computer-science"}, out_root=out)
        with open(sib) as fh:
            assert fh.read() == "SENTINEL"                  # sibling profile untouched

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

import os
import hashlib
from facultyfolio import build, photos


def test_build_koutis(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda *a, **k: "monogram:IK")
    path = build.build_one("ikoutis", str(tmp_path))
    assert os.path.exists(path)
    html = open(path).read()
    assert "Ioannis Koutis" in html


def test_build_all_and_leaderboard(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")
    from facultyfolio import db, config
    res = build.build_all(str(tmp_path))
    # a profile per YWCC home faculty across all three depts (57 + 21 + 41)
    depts = db.dept_orgs_of_college(db.org_node_by_slug(config.COLLEGE_SLUG))
    assert res["count"] == sum(len(db.faculty_slugs(d["node_id"])) for d in depts)
    # each dept leaderboard exists at its org slug; college-administration never gets one
    assert os.path.exists(os.path.join(tmp_path, "computer-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "data-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "informatics", "index.html"))
    assert not os.path.exists(os.path.join(tmp_path, "college-administration", "index.html"))
    # hub at root, profiles flat, legacy cs/ redirect preserved, assets copied
    assert os.path.exists(os.path.join(tmp_path, "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "p", "ikoutis.html"))
    assert os.path.exists(os.path.join(tmp_path, "assets", "style.css"))
    cs_redirect = open(os.path.join(tmp_path, "cs", "index.html")).read()
    assert "url=../computer-science/index.html" in cs_redirect
    hub = open(os.path.join(tmp_path, "index.html")).read()
    assert "Ying Wu College of Computing" in hub


def test_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")

    def digest():
        out = {}
        for root, _dirs, files in os.walk(tmp_path):
            for f in sorted(files):
                if f.endswith(".html"):
                    p = os.path.join(root, f)
                    out[os.path.relpath(p, tmp_path)] = hashlib.md5(open(p, "rb").read()).hexdigest()
        return out

    build.build_all(str(tmp_path))
    h1 = digest()
    build.build_all(str(tmp_path))
    h2 = digest()
    assert h1 == h2                    # byte-identical rebuild

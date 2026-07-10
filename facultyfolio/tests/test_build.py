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
    from facultyfolio import db
    res = build.build_all(str(tmp_path))
    # count == DISTINCT home faculty across published YWCC depts (dedup-safe)
    depts = db.dept_orgs_of_college(db.org_node_by_slug("ywcc"))
    distinct = set()
    for d in depts:
        distinct.update(db.faculty_slugs(d["node_id"]))
    assert res["count"] == len(distinct)
    # dept leaderboards now nested under the college; admin unit still gets none
    assert os.path.exists(os.path.join(tmp_path, "ywcc", "computer-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "ywcc", "data-science", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "ywcc", "informatics", "index.html"))
    assert not os.path.exists(os.path.join(tmp_path, "ywcc", "college-administration", "index.html"))
    # root = NJIT hub; YWCC hub moved under /ywcc/; profiles flat; assets copied
    root_hub = open(os.path.join(tmp_path, "index.html")).read()
    assert "New Jersey Institute of Technology" in root_hub
    college_hub = open(os.path.join(tmp_path, "ywcc", "index.html")).read()
    assert "Ying Wu College of Computing" in college_hub
    assert os.path.exists(os.path.join(tmp_path, "p", "ikoutis.html"))
    assert os.path.exists(os.path.join(tmp_path, "assets", "style.css"))
    # legacy /cs/ and /computer-science/ redirect to the nested dept
    cs_redirect = open(os.path.join(tmp_path, "cs", "index.html")).read()
    assert "url=../ywcc/computer-science/index.html" in cs_redirect


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


def test_college_hub_has_stats_and_leadership(tmp_path, monkeypatch):
    from facultyfolio import build
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")
    build.build_site(scope={"college": "ywcc"}, out_root=str(tmp_path))
    html = (tmp_path / "ywcc" / "index.html").read_text()
    assert "119" in html and "3 · Department Chair" in html          # rollup
    assert "Jamie Payton" in html                                    # dean
    assert "David Bader" in html and "Brook Wu" in html              # assoc deans (post-fix)
    assert "Vincent Oria" in html and "Department Chair, Computer Science" in html
    assert "p/bader.html" in html and "p/oria.html" in html          # leaders linked
    # ordering: department cards above the Dean section
    assert html.index("computer-science/index.html") < html.index("Jamie Payton")
